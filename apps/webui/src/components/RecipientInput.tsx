import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent, ReactElement } from "react";
import {
  commitRecipients,
  searchRecipients,
} from "../lib/recipients";
import type { Recipient, RecipientCandidate, RecipientField } from "../lib/recipients";
import type { ParticipantInfo } from "../types";

export function RecipientInput({
  label,
  value,
  onChange,
  participants,
  autoFocus,
  disabled,
}: {
  label: string;
  value: RecipientField;
  onChange: (value: RecipientField) => void;
  participants: ParticipantInfo[];
  autoFocus?: boolean;
  disabled?: boolean;
}): ReactElement {
  const id = useId();
  const fieldRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const optionsRef = useRef<HTMLDivElement>(null);
  const composing = useRef(false);
  const outsidePointer = useRef(false);
  const deferredBlur = useRef(false);
  const latest = useRef({ value, participants, onChange, disabled });
  latest.current = { value, participants, onChange, disabled };
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [error, setError] = useState<string>();
  const candidates = searchRecipients(participants, value.draft, value.recipients);
  const directory = searchRecipients(participants, "", []);
  const activeIndex = Math.min(highlight, Math.max(0, candidates.length - 1));
  const showOptions = open && !disabled;
  const listId = `${id}-options`;
  const errorId = `${id}-error`;
  const chipLabel = (recipient: Recipient): string => {
    const ambiguous = [...directory, ...value.recipients].some((other) =>
      other.label === recipient.label && other.address !== recipient.address);
    return ambiguous && recipient.label !== recipient.address
      ? `${recipient.label} <${recipient.address}>`
      : recipient.label;
  };

  useEffect(() => {
    const finishBlur = (): void => {
      if (!deferredBlur.current) return;
      deferredBlur.current = false;
      setOpen(false);
      const current = latest.current;
      if (!current.disabled && !composing.current && current.value.draft.trim()) {
        const result = commitRecipients(current.value, current.participants);
        current.onChange(result.field);
        setError(result.error);
        setHighlight(0);
      }
    };
    const pointerDown = (event: PointerEvent): void => {
      outsidePointer.current = event.target instanceof Node
        && !Boolean(fieldRef.current?.contains(event.target));
    };
    const click = (event: MouseEvent): void => {
      const outside = event.target instanceof Node
        && !Boolean(fieldRef.current?.contains(event.target));
      outsidePointer.current = false;
      if (outside) deferredBlur.current = true;
      // Keep the inline list in place through mouseup and the target's handler.
      // Read the latest controlled value after React processes that handler.
      queueMicrotask(finishBlur);
    };
    const pointerCancel = (): void => {
      outsidePointer.current = false;
      finishBlur();
    };
    document.addEventListener("pointerdown", pointerDown, true);
    document.addEventListener("click", click, true);
    document.addEventListener("pointercancel", pointerCancel);
    return () => {
      deferredBlur.current = false;
      document.removeEventListener("pointerdown", pointerDown, true);
      document.removeEventListener("click", click, true);
      document.removeEventListener("pointercancel", pointerCancel);
    };
  }, []);

  useEffect(() => {
    // Scroll the list itself: scrollIntoView can also move the enclosing dialog.
    const list = optionsRef.current;
    const active = list?.children[activeIndex] as HTMLElement | undefined;
    if (!list || !active || !showOptions) return;
    const top = active.getBoundingClientRect().top - list.getBoundingClientRect().top
      + list.scrollTop - list.clientTop;
    const bottom = top + active.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) {
      list.scrollTop = bottom - list.clientHeight;
    }
  }, [activeIndex, showOptions, value.draft, candidates.length]);

  const commit = (field: RecipientField): boolean => {
    const result = commitRecipients(field, participants);
    onChange(result.field);
    setError(result.error);
    setHighlight(0);
    return !result.error;
  };

  const choose = (candidate: RecipientCandidate): void => {
    if (disabled) return;
    onChange({
      recipients: value.recipients.some((recipient) => recipient.address === candidate.address)
        ? value.recipients
        : [...value.recipients, { address: candidate.address, label: candidate.label }],
      draft: "",
    });
    setError(undefined);
    setHighlight(0);
    inputRef.current?.focus({ preventScroll: true });
    setOpen(false);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (disabled) return;
    if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) {
      // In particular, never let an IME confirmation trigger the dialog shortcut.
      event.stopPropagation();
      return;
    }
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) return;
    if (event.key === "Tab") {
      if (value.draft.trim()) commit(value);
      setOpen(false);
      return;
    }
    if (event.key === "Escape") {
      if (showOptions) {
        event.stopPropagation();
        setOpen(false);
      }
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      if (candidates.length > 0) {
        const step = event.key === "ArrowDown" ? 1 : -1;
        setHighlight(showOptions
          ? (activeIndex + step + candidates.length) % candidates.length
          : step > 0 ? 0 : candidates.length - 1);
      }
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      if (/^[^\s@,;，；]+@[^\s@,;，；]+$/.test(value.draft.trim())) {
        if (commit(value)) setOpen(false);
        return;
      }
      const candidate = showOptions ? candidates[activeIndex] : undefined;
      if (candidate) choose(candidate);
      else if (value.draft.trim() && commit(value)) setOpen(false);
      return;
    }
    if (event.key === "Backspace" && !value.draft && value.recipients.length > 0) {
      event.preventDefault();
      onChange({ ...value, recipients: value.recipients.slice(0, -1) });
      setError(undefined);
    }
  };

  return (
    <div
      ref={fieldRef}
      className="mail-form-row recipient-field"
      onBlur={(event) => {
        if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
        if (outsidePointer.current) {
          deferredBlur.current = true;
          return;
        }
        setOpen(false);
        if (!disabled && !composing.current && value.draft.trim()) commit(value);
      }}
    >
      <label className="tech-label" htmlFor={id}>{label}</label>
      <div className="input recipient-control">
        {value.recipients.map((recipient) => (
          <span className="recipient-chip" key={recipient.address} title={recipient.address}>
            <span className="recipient-chip-label">{chipLabel(recipient)}</span>
            <button
              className="recipient-remove"
              type="button"
              disabled={disabled}
              aria-label={`Remove ${recipient.label === recipient.address
                ? recipient.label : `${recipient.label} <${recipient.address}>`} from ${label}`}
              onClick={() => {
                onChange({ ...value, recipients: value.recipients.filter((item) => item.address !== recipient.address) });
                setError(undefined);
                inputRef.current?.focus({ preventScroll: true });
                setOpen(false);
              }}
            >×</button>
          </span>
        ))}
        <input
          id={id}
          ref={inputRef}
          className="recipient-query"
          role="combobox"
          autoComplete="off"
          aria-autocomplete="list"
          aria-expanded={showOptions}
          aria-controls={showOptions ? listId : undefined}
          aria-activedescendant={showOptions && candidates.length > 0 ? `${id}-option-${activeIndex}` : undefined}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : undefined}
          placeholder="Search name, ID, or address"
          value={value.draft}
          autoFocus={autoFocus}
          disabled={disabled}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onCompositionStart={() => { composing.current = true; }}
          onCompositionEnd={() => { composing.current = false; }}
          onKeyDown={onKeyDown}
          onChange={(event) => {
            const field = { ...value, draft: event.target.value };
            if (!composing.current && /[,;，；\r\n]/.test(field.draft)) {
              setOpen(!commit(field));
            } else {
              onChange(field);
              setError(undefined);
              setOpen(true);
            }
            setHighlight(0);
          }}
          onPaste={(event) => {
            const text = event.clipboardData.getData("text");
            if (composing.current || !/[,;，；\r\n]/.test(text)) return;
            event.preventDefault();
            const input = event.currentTarget;
            const start = input.selectionStart ?? value.draft.length;
            const end = input.selectionEnd ?? start;
            setOpen(!commit({ ...value, draft: value.draft.slice(0, start) + text + value.draft.slice(end) }));
          }}
        />
      </div>
      {showOptions ? (
        <div className="recipient-options" id={listId} ref={optionsRef} role="listbox" aria-label={`${label} suggestions`}>
          {candidates.map((candidate, index) => (
            <button
              type="button"
              role="option"
              tabIndex={-1}
              id={`${id}-option-${index}`}
              key={candidate.address}
              className={`recipient-option${index === activeIndex ? " recipient-option--active" : ""}`}
              aria-selected={index === activeIndex}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(candidate)}
            >
              <span className="recipient-option-name">{candidate.label}</span>
              <span className="recipient-option-address">{candidate.address}</span>
              <span className="recipient-option-type">
                {candidate.type === "human" && Number(participants.find((person) => person.id === candidate.id)?.metadata?.session_count ?? 0) > 0
                  ? <span className="mail-online">● ONLINE </span> : null}
                {candidate.type.toUpperCase()}
              </span>
            </button>
          ))}
          {candidates.length === 0 ? <div className="recipient-empty">No matching participants. Enter a full address to add a recipient.</div> : null}
        </div>
      ) : null}
      {error ? <div className="recipient-error" id={errorId} role="alert">{error}</div> : null}
    </div>
  );
}
