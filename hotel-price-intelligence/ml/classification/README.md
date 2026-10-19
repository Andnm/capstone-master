# Classification (reserved)

Reserved workspace for future up/down/stable classifiers. No classifier was implemented or trained
as part of the layout migration. Reuse `../shared/dataset_builder` and immutable exported datasets;
do not duplicate the feature/label pipeline here.

When implementation starts, put training code, configs, scripts, notebooks and tests in this task
directory. Generated models/results/Colab packages belong in `models/`, `results/`, `colab/` (ignored
by Git), with versioned manifests and an independently validated classification label contract.
