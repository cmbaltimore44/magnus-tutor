from magnus_tutor.tools.mathcheck import candidate_expressions, equivalent, matches_reference, numbers_in, numeric_value


def test_latex_and_plain_agree():
    assert equivalent(r"\frac{q}{4\pi\varepsilon_0 r^2}", "q/(4*pi*epsilon_0*r**2)") is True
    assert equivalent(r"\frac{\lambda}{2\pi\epsilon_0 r}", "lambda/(2*pi*epsilon_0*r)") is True
    assert equivalent(r"\sqrt{2gh}", "sqrt(2*g*h)") is True


def test_case_matters_and_physics_letters_are_symbols():
    assert equivalent("Q*r/(4*pi*epsilon_0*R**3)", r"\frac{Qr}{4\pi\varepsilon_0 R^3}") is True
    assert equivalent("Q*r/(4*pi*epsilon_0*r**3)", "Q*r/(4*pi*epsilon_0*R**3)") is False
    assert equivalent("E*q", "q*E") is True
    assert equivalent("i*hbar", "I*hbar") is True


def test_numbers():
    assert numbers_in(r"2.3\times10^{5} and 4.5 × 10^-3 and 3e8") == [230000.0, 0.0045, 3e8]
    assert abs(numeric_value("2.3e5 N/C") - 2.3e5) < 1e-6


def test_candidates_and_matching():
    assert "3.2 m/s" in candidate_expressions("The answer is 3.2 m/s.")
    assert matches_reference(r"I got $E=\frac{\lambda}{2\pi \epsilon_0 r}$", "", "lambda/(2*pi*epsilon_0*r)") is True
    assert matches_reference(r"I got $E=\frac{\lambda}{4\pi \epsilon_0 r}$", "", "lambda/(2*pi*epsilon_0*r)") is False
    assert matches_reference("no idea", "674 N/C") is None


def test_untrusted_math_is_never_evaluated(tmp_path):
    from magnus_tutor.engine.leak import LeakChecker
    from magnus_tutor.tools.mathcheck import to_sympy
    from magnus_tutor.tools.verify import run_check

    marker = tmp_path / "pwned"
    payloads = [f"__import__('os').system('touch {marker}')", f"'_'+'_imp'+'ort_'+'_(\"os\").system(\"touch {marker}\")'",
                "().__class__.__base__.__subclasses__()", "Symbol('x').__class__", "lambda: 1", "9**9**9", "2**100000"]
    for pl in payloads:
        assert to_sympy(pl) is None
        assert matches_reference(f"I got ${pl}$", "674 N/C") in (None, False)
        LeakChecker({"final_answer": "674", "final_answer_sympy": pl}, "").check(f"so $x = {pl}$", 1)
        run_check({"kind": "limit", "lhs": "x", "rhs": "0", "var": "x", "point": pl})
    assert not marker.exists()


def test_final_answer_only():
    assert matches_reference("I think it's 3 or 7 or 12 or 20", "12") is None
    assert matches_reference("I got x = 12 then v = 40", "12") is False
    assert matches_reference("The force is 1,500 N", "1500 N") is True
    assert equivalent("F/m", "F/m") is True and equivalent("exp(-x)", "e^(-x)") is True
