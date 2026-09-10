"""
Figure-generation scripts. Run from the repo root:

    python3 -m figures.make_figures          # data figures -> results/figures/
    python3 -m figures.make_topology_figure  # the real GEANT topology map

Every data figure is rendered straight from the CSV / JSON an experiment
wrote to results/ -- see each function's _load_csv() call for its source.
"""
