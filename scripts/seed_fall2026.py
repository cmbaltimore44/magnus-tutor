"""One-time: create the Fall 2026 courses (safe to re-run; skips existing ones)."""

from magnus_tutor import courses as C
from magnus_tutor.config import paths

p = paths()
SEED = [
    dict(slug="qm", title="Introduction to Quantum Physics", short="QM", kind=["physics", "math"],
         topics=["wave functions", "Schrödinger equation", "operators and observables", "infinite and finite wells",
                 "harmonic oscillator", "angular momentum", "hydrogen atom", "spin"],
         notation_conventions="Dirac notation; hbar written as \\hbar; operators with hats, e.g. \\hat{H}."),
    dict(slug="em", title="Electricity and Magnetism", short="E&M", kind=["physics", "math"],
         topics=["Coulomb's law", "electric fields", "Gauss's law", "electric potential", "capacitance", "current and resistance",
                 "magnetic fields", "Ampère's law", "Faraday's law", "inductance", "Maxwell's equations"],
         notation_conventions="SI units; k = 1/(4\\pi\\varepsilon_0); unit vectors with hats."),
    dict(slug="psl", title="Programming Systems and Languages", short="PSL", kind=["code"], languages=["sml"],
         topics=["functional programming", "pattern matching", "recursion", "higher-order functions", "type inference",
                 "datatypes", "modules and signatures", "interpreters"]),
    dict(slug="cloud", title="Cloud Computing with Big Data Applications", short="Cloud", kind=["code"],
         languages=["python", "java", "dockerfile", "kubernetes"],
         topics=["virtualization and containers", "Docker", "Kubernetes", "distributed storage", "MapReduce", "Spark", "cloud services"]),
    dict(slug="mhh", title="Magicians, Healers, and Holy Men", short="MHH", kind=["writing"],
         topics=[], description="Mostly writing: essays and close reading of primary and secondary sources."),
]
for d in SEED:
    if C.load_course(d["slug"], p):
        print(f"exists: {d['slug']}")
        continue
    slug = d.pop("slug")
    title = d.pop("title")
    c = C.create_course(title, slug=slug, term="Fall 2026", p=p, **{k: d[k] for k in ("short", "kind", "languages") if k in d},
                        **{k: d[k] for k in ("topics", "notation_conventions", "description") if k in d})
    print(f"created {c.slug}: {c.title}")
