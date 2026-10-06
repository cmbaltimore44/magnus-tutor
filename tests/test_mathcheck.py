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
