You are solving a problem privately so a tutor can guide a student. The student will
never see this. Solve it carefully and completely.

Course: {{course}}
Problem:
{{problem}}

Relevant course material (may be empty):
{{retrieved_context}}

Return JSON only, with these keys:
- "final_answer": the final answer as a short string (include units for physics).
- "final_answer_sympy": the final answer as a SymPy-parsable expression if it is
  mathematical (use symbols from the problem, e.g. "q/(4*pi*epsilon_0*r**2)"), else "".
- "units": SI units of the final answer if physical (pint syntax, e.g. "newton / coulomb"), else "".
- "steps": a list of short steps, each one sentence or one equation.
- "key_concepts": a list of the concepts the problem tests.
- "common_mistakes": a list of likely student mistakes.
- "checks": a list of objects {"kind": "sympy_equal" | "numeric" | "derivative" | "integral" | "solve" | "units",
  "lhs": "...", "rhs": "...", "var": "...", "note": "..."} that a computer algebra system
  could use to verify the work (optional, may be empty).
