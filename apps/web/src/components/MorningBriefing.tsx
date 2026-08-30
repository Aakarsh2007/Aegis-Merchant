/**
 * The morning briefing (§19.3).
 *
 * The restraint sentence at the bottom is the most important text in this
 * interface. An agent that reports **what it chose not to do** is one a
 * merchant can trust, and it is the only part of an agent demo a sceptic can
 * actually audit — every other panel shows actions taken, which is the half
 * that always looks good.
 *
 * It is rendered with emphasis, never collapsed, and never hidden when the
 * count is zero.
 */
import { api, type Briefing } from "@/lib/api";
import { FetchError, ProvenanceBadge } from "./Provenance";

export async function MorningBriefing() {
  const result = await api.briefing();
  if (!result.ok) {
    return <FetchError what="Briefing" error={result.error} />;
  }
  const b: Briefing = result.data;

  return (
    <section className="rounded-xl border border-ink-700 bg-gradient-to-b from-ink-900/80 to-ink-900/40 p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-ink-800 pb-3">
        <h2 className="text-base font-semibold text-paper-50">{b.greeting}</h2>
        <span className="numeric text-[11px] text-paper-500">
          {b.as_of_ist.replace("T", " · ").slice(0, 22)} IST
        </span>
      </header>

      <ul className="mt-3 space-y-1.5">
        {b.lines.map((line) => (
          <li key={line} className="flex gap-2 text-[12px] leading-relaxed text-paper-100">
            <span className="text-brand-500">›</span>
            <span>{line}</span>
          </li>
        ))}
      </ul>

      {/*
        The section that matters. Bordered and given its own heading so it
        cannot be mistaken for a footnote.
      */}
      <div className="mt-4 rounded-lg border border-brand-500/30 bg-brand-500/5 p-3">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-brand-400">
          What I chose not to do
        </p>
        <p className="mt-1.5 text-[12px] leading-relaxed text-paper-100">
          {b.restraint.sentence}
        </p>
        {b.restraint.items.length > 0 ? (
          <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
            {b.restraint.items.map((item) => (
              <li key={item.rule} className="numeric text-[10px] text-paper-500">
                {item.rule}
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      {b.caveats.length > 0 ? (
        <div className="mt-3 space-y-1">
          {b.caveats.map((caveat) => (
            <p key={caveat} className="text-[11px] leading-relaxed text-simulated-500">
              {caveat}
            </p>
          ))}
        </div>
      ) : null}

      <footer className="mt-3 flex items-center gap-2 border-t border-ink-800 pt-3">
        <ProvenanceBadge
          provenance="SIMULATED"
          basis={b.headline.gross_simulated?.basis ?? "computed over the seeded corpus"}
        />
        <p className="text-[10px] leading-snug text-paper-500">{b.narration}</p>
      </footer>
    </section>
  );
}
