import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, api } from "../api/client";
import type { ClinicProfile, Owner, Patient } from "../api/types";
import { Combobox } from "./Combobox";
import { Button, ErrorBanner, Field, inputStyle, useApiErrorMessage } from "./ui";
import { PinDialog } from "./PinDialog";

/** Cadastro do paciente: responsável, dados e identificadores, num passo só.
 *
 *  Os identificadores NÃO são campos fixos: vêm do perfil de compliance da
 *  clínica (microchip e RGA na veterinária; CPF, CNS e prontuário na saúde
 *  humana). É por isso que este componente serve aos dois mercados sem `if`. */
export function PatientsQuickCreate({
  owners,
  profile,
  initialName = "",
  onCreated,
  onClose,
}: {
  owners: Owner[];
  profile?: ClinicProfile | null;
  /** O que a pessoa digitou na busca antes de desistir de achar. */
  initialName?: string;
  onCreated: (patient: Patient) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const describeError = useApiErrorMessage();

  const [ownerId, setOwnerId] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [phone, setPhone] = useState("");
  const [name, setName] = useState(initialName);
  const [species, setSpecies] = useState("");
  const [breed, setBreed] = useState("");
  const [weight, setWeight] = useState("");
  const [taxId, setTaxId] = useState("");
  const [identifiers, setIdentifiers] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [askPin, setAskPin] = useState(false);

  const newOwner = ownerId === "";
  const kinds = profile?.patient_identifier_kinds ?? [];
  const responsible = t(profile?.responsible_label_key ?? "responsible.owner");
  const ready = name.trim() !== "" && species.trim() !== "" && (!newOwner || (ownerName.trim() !== "" && phone.trim() !== ""));

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      // Uma chamada só: o servidor cria responsável, paciente e identificadores
      // na mesma transação. Meio cadastro salvo seria pior que nenhum.
      const patient = await api.registerPatient({
        name: name.trim(),
        species: species.trim(),
        breed: breed.trim() || null,
        weight_kg: weight ? Number(weight) : null,
        identifiers: kinds
          .filter((kind) => (identifiers[kind.kind] ?? "").trim() !== "")
          .map((kind) => ({ kind: kind.kind, value: identifiers[kind.kind].trim() })),
        ...(newOwner
          ? {
              owner_name: ownerName.trim(),
              owner_phone_e164: phone.trim(),
              owner_tax_id: taxId.trim() || null,
            }
          : { owner_id: ownerId }),
      });
      onCreated(patient);
    } catch (err) {
      if (err instanceof ApiError && err.code === "operator_required") {
        setAskPin(true);
        return;
      }
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  if (askPin) {
    return (
      <PinDialog
        context={t("patients.quick.title")}
        onDone={() => {
          setAskPin(false);
          void submit();
        }}
        onCancel={() => setAskPin(false)}
      />
    );
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <form
        className="modal-card"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <h2 style={{ fontSize: 20 }}>{t("patients.quick.title")}</h2>
        <ErrorBanner message={error} />

        <Field label={responsible}>
          <Combobox
            value={ownerId}
            onChange={setOwnerId}
            emptyLabel={t("patients.quick.newOwner")}
            placeholder={t("patients.quick.chooseOwner")}
            options={owners.map((owner) => ({
              value: owner.id,
              label: owner.name,
              hint: [owner.phone_e164, owner.tax_id].filter(Boolean).join(" · "),
              // Achar o responsável pelo documento é o caminho da recepção.
              keywords: owner.tax_id ?? undefined,
            }))}
          />
        </Field>

        {newOwner ? (
          <div className="form-grid-2">
            <Field label={t("patients.quick.ownerNameOf", { responsible })}>
              <input
                style={inputStyle}
                value={ownerName}
                onChange={(event) => setOwnerName(event.target.value)}
                required
              />
            </Field>
            <Field label={t("patients.quick.phone")}>
              <input
                style={inputStyle}
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
                placeholder={t("patients.quick.phonePlaceholder")}
                pattern="\+[1-9][0-9]{7,14}"
                required
              />
            </Field>
            <Field label={t("patients.quick.taxId")}>
              <input
                style={inputStyle}
                value={taxId}
                onChange={(event) => setTaxId(event.target.value)}
                placeholder={t("patients.quick.taxIdPlaceholder")}
              />
            </Field>
          </div>
        ) : null}

        <div className="form-grid-2">
          <Field label={t("patients.quick.name")}>
            <input
              style={inputStyle}
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </Field>
          <Field label={t("patients.quick.species")}>
            <input
              style={inputStyle}
              value={species}
              onChange={(event) => setSpecies(event.target.value)}
              placeholder={t("patients.quick.speciesPlaceholder")}
              required
            />
          </Field>
          <Field label={t("patients.quick.breed")}>
            <input
              style={inputStyle}
              value={breed}
              onChange={(event) => setBreed(event.target.value)}
            />
          </Field>
          <Field label={t("patients.quick.weight")}>
            <input
              style={inputStyle}
              type="number"
              min={0}
              step="0.1"
              value={weight}
              onChange={(event) => setWeight(event.target.value)}
            />
          </Field>
        </div>

        {kinds.length > 0 ? (
          <div className="form-grid-2">
            {kinds.map((kind) => (
              <Field key={kind.kind} label={t(kind.label_key)}>
                <input
                  style={inputStyle}
                  value={identifiers[kind.kind] ?? ""}
                  onChange={(event) =>
                    setIdentifiers((current) => ({
                      ...current,
                      [kind.kind]: event.target.value,
                    }))
                  }
                  inputMode={kind.pattern?.includes("\\d") ? "numeric" : undefined}
                />
              </Field>
            ))}
          </div>
        ) : null}

        <div className="patients-actions">
          <Button type="submit" disabled={busy || !ready}>
            {t("patients.quick.submit")}
          </Button>
          <Button variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
        </div>
      </form>
    </div>
  );
}
