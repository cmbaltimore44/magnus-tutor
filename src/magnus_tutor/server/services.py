"""Wire the long-lived services (solver, retriever, timer bridge) into AppState."""

from __future__ import annotations

from ..solver import SolverService
from .events import bus


def init_services(st) -> None:
    if "retriever" not in st.extras:
        try:
            from ..retrieval import Retriever

            st.extras["retriever"] = Retriever(st.db, st.models)
        except ModuleNotFoundError:
            st.extras["retriever"] = None
    if "solver" not in st.extras:
        st.extras["solver"] = SolverService(st.p, st.db, st.models, lambda: st.settings, retriever=st.extras.get("retriever"), publish=bus.publish)
    st.extras.pop("tutor", None)  # rebuilt with the services on first use

    from ..llm.cloud import configure_cloud

    configure_cloud(st)
