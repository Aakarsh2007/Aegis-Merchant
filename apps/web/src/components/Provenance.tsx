/**
 * The badge every rupee figure wears (§14.5).
 *
 * `MoneyTile` takes a `Figure`, and a `Figure` cannot exist without a
 * provenance and a basis — so there is no code path through this component
 * that renders an unqualified number. That is the same guarantee the API
 * makes, restated at the last place a human sees it.
 *
 * The `basis` is a `title` attribute rather than a tooltip library: a judge
 * hovering a number gets the sentence explaining how it was computed, with no
 * dependency and nothing to fail on stage.
 */
import type { Count, Figure, Provenance } from "@/lib/api";

const STYLES: Record<Provenance, { chip: string; label: string; hint: string }> = {
  RAZORPAY_VERIFIED: {
    chip: "bg-verified-500/15 text-verified-500 ring-verified-500/30",
    label: "RAZORPAY VERIFIED",
    hint: "A signed Razorpay webhook proves this money moved.",
  },
  SIMULATED: {
    chip: "bg-simulated-500/15 text-simulated-500 ring-simulated-500/30",
    label: "SIMULATED",
    hint: "Real machinery over the seeded corpus; customer responses are a declared parameter.",
  },
  ESTIMATED: {
    chip: "bg-estimated-500/15 text-estimated-500 ring-estimated-500/30",
    label: "ESTIMATED",
    hint: "A projection at published rates, not a measurement.",
  },
};

export function ProvenanceBadge({
  provenance,
  basis,
}: {
  provenance: Provenance;
  basis?: string;
}) {
  const style = STYLES[provenance];
  return (
    <span
      title={basis ? `${style.hint}\n\n${basis}` : style.hint}
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wider ring-1 ring-inset ${style.chip}`}
    >
      {style.label}
    </span>
  );
}

export function MoneyTile({
  label,
  figure,
  emphasis = false,
  caption,
}: {
  label: string;
  figure: Figure;
  emphasis?: boolean;
  caption?: string;
}) {
  return (
    <div
      className={`rounded-xl border p-4 ${
        emphasis
          ? "border-brand-500/40 bg-brand-500/5"
          : "border-ink-700 bg-ink-900/60"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-medium text-paper-500">{label}</span>
        <ProvenanceBadge provenance={figure.provenance} basis={figure.basis} />
      </div>
      <div
        className={`numeric mt-2 ${emphasis ? "text-2xl" : "text-xl"} font-semibold ${
          figure.paise < 0 ? "text-danger-500" : "text-paper-50"
        }`}
        title={figure.basis}
      >
        {figure.display}
      </div>
      {caption ? (
        <p className="mt-1.5 text-[11px] leading-snug text-paper-500">{caption}</p>
      ) : null}
    </div>
  );
}

export function CountTile({
  label,
  count,
  caption,
}: {
  label: string;
  count: Count;
  caption?: string;
}) {
  return (
    <div className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-medium text-paper-500">{label}</span>
        <ProvenanceBadge provenance={count.provenance} basis={count.basis} />
      </div>
      <div className="numeric mt-2 text-xl font-semibold text-paper-50" title={count.basis}>
        {count.value.toLocaleString("en-IN")}
      </div>
      {caption ? (
        <p className="mt-1.5 text-[11px] leading-snug text-paper-500">{caption}</p>
      ) : null}
    </div>
  );
}

/**
 * The error state a failed fetch renders into.
 *
 * Deliberately loud. The alternative — a tile showing zero — is a number a
 * viewer will believe, and it is indistinguishable from a real result.
 */
export function FetchError({ what, error }: { what: string; error: string }) {
  return (
    <div className="rounded-xl border border-danger-500/40 bg-danger-500/5 p-4">
      <div className="text-xs font-semibold text-danger-500">
        {what} unavailable
      </div>
      <p className="mt-1 text-[11px] leading-snug text-paper-500">{error}</p>
      <p className="mt-2 text-[11px] text-paper-500">
        No figure is shown rather than a zero, because a zero is a number you
        would believe.
      </p>
    </div>
  );
}
