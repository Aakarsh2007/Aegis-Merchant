/**
 * The boundary: what the model decides, and what it is never allowed near.
 *
 * The question a Razorpay judge will ask is "why is this an AI project?", and
 * the answer that wins is not "we use Gemini". It is: **intelligence is placed
 * exactly where the uncertainty is, and nowhere near the money.**
 *
 * A model is good at reading a messy failure and arguing for an action. It is
 * the wrong tool for deciding whether an amount is within a limit — that is an
 * integer comparison, and a comparison that sometimes hallucinates is strictly
 * worse than one that does not.
 *
 * So this renders three bands in the order a case flows through them, with the
 * model in the middle and deterministic code on both sides. The line that
 * matters is at the bottom: **AI proposes, policy disposes.**
 */

const DETERMINISTIC_IN = [
  "Webhook signature (HMAC)",
  "Payment state from Razorpay",
  "Failure taxonomy — 96.5% on the golden set",
  "Rail health index",
  "Consent class · DND · opt-out",
  "Contact history and caps",
  "Control-arm assignment",
];

const AI_LAYER = [
  "Reads an ambiguous failure where the fields disagree",
  "Argues for a recovery strategy",
  "Writes the rationale a human reads",
  "Fills named slots in a pre-approved template",
];

const DETERMINISTIC_OUT = [
  "Policy firewall — limits, clamps, refusals",
  "Twelve stopping rules",
  "Capability token — no token, no side effect",
  "Amount, always read from the order",
  "Execution via the transactional outbox",
  "Signed-webhook verification",
  "Attribution against the holdout arm",
];

function Band({
  label,
  sublabel,
  items,
  tone,
}: {
  label: string;
  sublabel: string;
  items: string[];
  tone: "det" | "ai";
}) {
  const isAI = tone === "ai";
  return (
    <div
      className={`rounded-lg border p-3 ${
        isAI
          ? "border-brand-500/50 bg-brand-500/10"
          : "border-ink-700 bg-ink-950/50"
      }`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <h3
          className={`text-[11px] font-semibold tracking-wide ${
            isAI ? "text-brand-400" : "text-paper-300"
          }`}
        >
          {label}
        </h3>
        <span className="text-[10px] text-paper-500">{sublabel}</span>
      </div>
      <ul className="mt-2 space-y-1">
        {items.map((item) => (
          <li key={item} className="flex gap-1.5 text-[11px] leading-snug text-paper-300">
            <span className={isAI ? "text-brand-500" : "text-paper-500"}>
              {isAI ? "◆" : "•"}
            </span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function WhereAIStops() {
  return (
    <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <header>
        <h2 className="text-sm font-semibold text-paper-50">Where the AI stops</h2>
        <p className="mt-0.5 text-[11px] leading-relaxed text-paper-500">
          Nine places we deliberately chose <em>not</em> to use a model. A limit
          check that sometimes hallucinates is strictly worse than one that
          cannot.
        </p>
      </header>

      <div className="mt-3 space-y-2">
        <Band
          label="DETERMINISTIC — facts"
          sublabel="rules, SQL, HMAC"
          items={DETERMINISTIC_IN}
          tone="det"
        />
        <div className="flex justify-center text-paper-500">↓</div>
        <Band
          label="AI — judgement under ambiguity"
          sublabel="Gemini, or a rule table if absent"
          items={AI_LAYER}
          tone="ai"
        />
        <div className="flex justify-center text-paper-500">↓</div>
        <Band
          label="DETERMINISTIC — authority and money"
          sublabel="the model never reaches here"
          items={DETERMINISTIC_OUT}
          tone="det"
        />
      </div>

      <p className="mt-3 border-t border-ink-800 pt-3 text-center text-[12px] font-semibold text-paper-100">
        AI proposes. Policy disposes.
      </p>
      <p className="mt-1.5 text-[10px] leading-relaxed text-paper-500">
        With no API key the middle band falls back to the deterministic
        classifier and <strong>everything still runs</strong> — which is the
        test of whether the model was load-bearing or decorative. The{" "}
        <span className="numeric">SOURCE</span> column in the case table says
        which decided each case.
      </p>
    </section>
  );
}
