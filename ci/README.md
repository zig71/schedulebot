# This file is a GitHub Actions workflow, kept outside .github/workflows/ because
# the local gh token lacks the `workflow` scope and GitHub rejects pushes that
# create workflow files without it.
#
# To enable CI:
#   gh auth refresh -s workflow
#   git mv ci/core-workflow.yml .github/workflows/core.yml && git commit -m "ci: enable core workflow" && git push
