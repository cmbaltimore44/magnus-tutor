// The hint ladder: where this problem stands, always visible. Level 4 (the full
// solution) is only reached deliberately, through "Show me the solution".
const STEPS = ['Your attempt', 'Concept', 'Method', 'Next step', 'Solution'];

export function HintLadder({ level, solved }: { level: number; solved?: boolean }) {
  return (
    <div className="ladder" aria-label={`Hint level ${level} of 4`}>
      {STEPS.map((s, i) => (
        <div key={s} className={`rung ${i <= level ? 'reached' : ''} ${i === level ? 'current' : ''} ${i === 4 ? 'last' : ''}`}>
          <span className="dot">{i === 4 && solved ? '✓' : i}</span>
          <span className="rung-label">{s}</span>
        </div>
      ))}
    </div>
  );
}
