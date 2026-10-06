You are solving a problem privately so a tutor can guide a student. The student will
never see this. Solve it carefully and completely, then double-check your arithmetic.

Course: {{course}}
Problem:
{{problem}}

Relevant course material (may be empty):
{{retrieved_context}}

Return JSON only, with these keys:
- "final_answer": the final answer as a short string (include units for physics).
- "final_answer_sympy": the final answer as a SymPy expression if it is mathematical
  (numbers, or symbols from the problem, e.g. "q/(4*pi*epsilon_0*r**2)"), else "".
- "units": SI units of the final answer if physical (pint syntax, e.g. "newton / coulomb"), else "".
- "symbol_units": for a symbolic physics answer, the SI unit of every symbol in
  final_answer_sympy, e.g. {"q": "coulomb", "r": "meter", "epsilon_0": "farad / meter"}, else {}.
- "steps": a list of short steps, each one sentence or one equation.
- "key_concepts": a list of the concepts the problem tests.
- "common_mistakes": a list of likely student mistakes.
- "checks": computer-algebra checks of your work, a list of objects
  {"kind": "numeric" | "sympy_equal" | "derivative" | "integral" | "solve" | "limit" | "units",
   "lhs": "...", "rhs": "...", "var": "x"}.
  numeric: lhs is an arithmetic expression from the given values, rhs the number it equals.
  derivative/integral: d(lhs)/d(var) == rhs, or the antiderivative of lhs is rhs.
  solve: lhs is an equation "a = b" and rhs is your solution for var.
  Use plain SymPy syntax (** for powers, sqrt(), pi, exp()). Include at least one check
  that recomputes the final answer.
For programming problems also include:
- "language": one of python, sml, java, ruby.
- "reference_code": a complete program that reads input from stdin and prints the answer.
- "tests": a list of {"name": "...", "stdin": "...", "expected": "..."} (expected stdout).
