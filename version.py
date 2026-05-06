# Decifer Trading — semantic version
#
# Convention (enforced automatically by .githooks/commit-msg):
#   MAJOR — breaking architectural overhaul  (feat! / BREAKING CHANGE)
#   MINOR — new feature shipped              (feat)
#   PATCH — bug fix, tweak, refactor, test   (everything else)
#
# To force a specific version or change the codename:
#   ./scripts/bump-version.sh <MAJOR.MINOR.PATCH> "<Codename>"


__version__ = "3.7.15"
__codename__ = "Apex"
