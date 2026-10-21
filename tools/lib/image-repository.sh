# shellcheck shell=sh
# The repository's name as a registry reference.
#   image_repository REPOSITORY
# An image reference's repository name must be lowercase, and a repository
# whose owner has capitals cannot be pushed to under its own name. The
# controller derives the same name from the source the image carries
# (controller/host/deploy.go), so a pipeline that builds a different name
# publishes an image the controller will not accept.
image_repository() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}
