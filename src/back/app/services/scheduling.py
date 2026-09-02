import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.i18n.catalog import translate
from app.models import Clinic, Prescription, Task

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

HORIZON_STEP = timedelta(days=1)
UTC_ZONE = ZoneInfo("UTC")

#: Quanto do futuro fica materializado em tarefas. Uma prescrição grava esta
#: janela na hora em que nasce; o job a estende de hora em hora. Estava
#: repetido em quatro arquivos, e o job que a mantém não rodava, então a ficha
#: de uma internação de mais de dois dias esvaziava sozinha.
HORIZON_HOURS = 48
SCHEDULING_HORIZON = timedelta(hours=HORIZON_HOURS)


def local_to_utc(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    """Converte 'HH:MM' de um dia local para UTC.

    Regra 7 do contrato: numa virada de horário de verão a hora local pode não
    existir (adiantamento) ou ser ambígua (atraso). Quando não existe,
    empurramos para a próxima hora válida; quando é ambígua, usamos a primeira
    ocorrência (fold=0).
    """
    hour, minute = (int(part) for part in hhmm.split(":"))
    naive = datetime.combine(day, time(hour, minute))
    candidate = naive.replace(tzinfo=tz, fold=0)
    # Hora inexistente: o offset "salta", então a ida-e-volta não bate.
    if candidate.astimezone(UTC_ZONE).astimezone(tz).replace(tzinfo=None) != naive:
        candidate = (naive + timedelta(hours=1)).replace(tzinfo=tz, fold=0)
    return candidate.astimezone(UTC_ZONE)


class SchedulingService:
    @staticmethod
    def generate(prescription: Prescription, clinic: Clinic, until: datetime) -> list[Task]:
        """Contrato do brief, regras 1 a 8. Função PURA: monta objetos Task em
        memória (quem persiste é a Task 11) e não toca em banco nem em relógio."""
        if prescription.kind == "prn":  # regra 1
            return []

        horizon = until  # regra 2
        if prescription.ends_at is not None:
            horizon = min(horizon, prescription.ends_at)
        start = prescription.starts_at
        if horizon <= start and not prescription.first_dose_now:
            return []

        tz = ZoneInfo(clinic.timezone)
        # A âncora da PRESCRIÇÃO vence a da clínica.
        #
        # As cerimônias do dia nascem com `details["anchor"]`: 16:00 para o
        # contato com o tutor, 08:00 para a evolução, que são as horas da rotina
        # real (SOP do HV-UFMS). O campo era gravado e nunca lido: as duas
        # caíam em `clinic.anchors["1440"]` = 10:00, no mesmo minuto, enquanto a
        # tela de admissão anunciava "todo dia às 16:00". O sistema prometia uma
        # hora e agendava outra.
        anchors = clinic.anchors.get(str(prescription.frequency_minutes))
        propria = (prescription.details or {}).get("anchor")
        if isinstance(propria, str) and _HHMM.match(propria):
            anchors = [propria]
        moments: list[datetime] = []

        if prescription.first_dose_now:  # regra 5 (parte 1)
            moments.append(start)

        if anchors:  # regra 3
            ordered = sorted(anchors)
            day = start.astimezone(tz).date()
            while True:
                for hhmm in ordered:
                    moment = local_to_utc(day, hhmm, tz)
                    if moment < start:
                        continue
                    if moment > horizon:
                        break
                    moments.append(moment)
                day += HORIZON_STEP
                if local_to_utc(day, ordered[0], tz) > horizon:
                    break
        else:  # regra 4
            step = timedelta(minutes=prescription.frequency_minutes)
            moment = start
            while moment <= horizon:
                if not (prescription.first_dose_now and moment == start):
                    moments.append(moment)
                moment += step

        if prescription.first_dose_now and anchors:  # regra 5 (parte 2)
            gap = timedelta(minutes=prescription.frequency_minutes - prescription.tolerance_minutes)
            moments = [moment for moment in moments if moment == start or moment - start >= gap]

        title = prescription.name
        if prescription.kind == "continuous":  # regra 6
            title = translate("task.check", clinic.locale, name=prescription.name)

        return [
            Task(
                clinic_id=prescription.clinic_id,
                hospitalization_id=prescription.hospitalization_id,
                prescription_id=prescription.id,
                title=title,
                category=prescription.category,
                scheduled_for=moment,
                criticality=prescription.criticality,
                tolerance_minutes=prescription.tolerance_minutes,
                price_minor=prescription.price_minor,
            )
            for moment in sorted(set(moments))
        ]
