import { useEffect, useMemo, useRef, useState } from "react";
import type { ElicitField, Elicitation } from "./useVoiceAgent";

/**
 * The question a paused tool stopped to ask, rendered as something you can
 * answer with your hands. You can always just say the answer instead — this is
 * the same question, on screen, for when a typed address beats a spoken one.
 */
export default function Elicit({
  ask,
  onAnswer,
}: {
  ask: Elicitation;
  onAnswer: (id: string, content: Record<string, unknown> | null) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const firstRef = useRef<HTMLInputElement | HTMLSelectElement>(null);
  const cardRef = useRef<HTMLElement>(null);

  // The stage scrolls and the orb fills it, so a question that arrives while
  // you're looking at the orb would otherwise open out of sight.
  useEffect(() => {
    cardRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [ask.id]);

  useEffect(() => {
    setValues(
      Object.fromEntries(
        ask.fields.map((f) => [f.name, f.default == null ? "" : String(f.default)]),
      ),
    );
  }, [ask.id, ask.fields]);

  useEffect(() => {
    firstRef.current?.focus({ preventScroll: true });
  }, [ask.id]);

  // A single yes-or-no is a confirmation, not a form — it gets two buttons.
  const confirmField = useMemo(
    () => (ask.fields.length === 1 && ask.fields[0].type === "boolean" ? ask.fields[0] : null),
    [ask.fields],
  );

  const complete = ask.fields.every((f) => !f.required || (values[f.name] ?? "").trim());

  const submit = (content: Record<string, unknown>) => {
    if (ask.sent) return;
    onAnswer(ask.id, content);
  };

  const submitForm = () => {
    const typed: Record<string, unknown> = {};
    for (const f of ask.fields) {
      const raw = (values[f.name] ?? "").trim();
      if (!raw && !f.required) continue;
      typed[f.name] =
        f.type === "boolean" ? raw === "true"
        : f.type === "number" || f.type === "integer" ? Number(raw)
        : raw;
    }
    submit(typed);
  };

  return (
    <section ref={cardRef} className={`elicit${ask.sent ? " sent" : ""}`} aria-live="polite">
      <div className="elicit-head">
        <span className="elicit-badge">{ask.server || "Fraise"} needs one thing</span>
        <span className="elicit-hint">{ask.sent ? "Sent" : "Or just say it out loud"}</span>
      </div>

      <p className="elicit-question">{ask.message}</p>

      {confirmField ? (
        <div className="elicit-actions">
          <button
            className="elicit-go"
            disabled={ask.sent}
            onClick={() => submit({ [confirmField.name]: true })}
          >
            Yes, go ahead
          </button>
          <button
            className="elicit-no"
            disabled={ask.sent}
            onClick={() => submit({ [confirmField.name]: false })}
          >
            No
          </button>
        </div>
      ) : (
        <form
          className="elicit-form"
          onSubmit={(e) => {
            e.preventDefault();
            submitForm();
          }}
        >
          {ask.fields.map((f, i) => (
            <Field
              key={f.name}
              field={f}
              value={values[f.name] ?? ""}
              disabled={ask.sent}
              inputRef={i === 0 ? firstRef : undefined}
              onChange={(v) => setValues((prev) => ({ ...prev, [f.name]: v }))}
            />
          ))}
          <div className="elicit-actions">
            <button className="elicit-go" type="submit" disabled={ask.sent || !complete}>
              Send it back
            </button>
            <button
              className="elicit-no"
              type="button"
              disabled={ask.sent}
              onClick={() => onAnswer(ask.id, null)}
            >
              Not now
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function Field({
  field,
  value,
  disabled,
  inputRef,
  onChange,
}: {
  field: ElicitField;
  value: string;
  disabled: boolean;
  inputRef?: React.Ref<HTMLInputElement | HTMLSelectElement>;
  onChange: (value: string) => void;
}) {
  const id = `elicit-${field.name}`;
  return (
    <label className="elicit-field" htmlFor={id}>
      <span className="elicit-label">{field.label}</span>
      {field.type === "enum" ? (
        <select
          id={id}
          ref={inputRef as React.Ref<HTMLSelectElement>}
          className="elicit-input"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">Choose…</option>
          {(field.choices ?? []).map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : field.type === "boolean" ? (
        <select
          id={id}
          ref={inputRef as React.Ref<HTMLSelectElement>}
          className="elicit-input"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">Choose…</option>
          <option value="true">Yes</option>
          <option value="false">No</option>
        </select>
      ) : (
        <input
          id={id}
          ref={inputRef as React.Ref<HTMLInputElement>}
          className="elicit-input"
          type={field.type === "string" ? "text" : "number"}
          step={field.type === "integer" ? 1 : "any"}
          value={value}
          disabled={disabled}
          placeholder={field.description}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {/* Text inputs already carry the description as placeholder text. */}
      {field.description && field.type !== "string" && field.type !== "number" && field.type !== "integer" && (
        <span className="elicit-desc">{field.description}</span>
      )}
    </label>
  );
}
