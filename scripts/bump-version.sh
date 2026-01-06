#!/bin/bash
# Bump version, commit, and tag for release
# Usage: ./scripts/bump-version.sh 1.0.1
#        ./scripts/bump-version.sh patch   # 1.0.0 -> 1.0.1
#        ./scripts/bump-version.sh minor   # 1.0.0 -> 1.1.0
#        ./scripts/bump-version.sh major   # 1.0.0 -> 2.0.0

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$ROOT_DIR"

# Get current version from pyproject.toml
CURRENT_VERSION=$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')

if [ -z "$1" ]; then
    echo "Current version: $CURRENT_VERSION"
    echo ""
    echo "Usage: $0 <version|patch|minor|major>"
    echo ""
    echo "Examples:"
    echo "  $0 1.0.1      # Set specific version"
    echo "  $0 patch      # $CURRENT_VERSION -> $(echo $CURRENT_VERSION | awk -F. '{print $1"."$2"."$3+1}')"
    echo "  $0 minor      # $CURRENT_VERSION -> $(echo $CURRENT_VERSION | awk -F. '{print $1"."$2+1".0"}')"
    echo "  $0 major      # $CURRENT_VERSION -> $(echo $CURRENT_VERSION | awk -F. '{print $1+1".0.0"}')"
    exit 1
fi

# Calculate new version
case "$1" in
    patch)
        NEW_VERSION=$(echo "$CURRENT_VERSION" | awk -F. '{print $1"."$2"."$3+1}')
        ;;
    minor)
        NEW_VERSION=$(echo "$CURRENT_VERSION" | awk -F. '{print $1"."$2+1".0"}')
        ;;
    major)
        NEW_VERSION=$(echo "$CURRENT_VERSION" | awk -F. '{print $1+1".0.0"}')
        ;;
    *)
        NEW_VERSION="$1"
        ;;
esac

# Validate version format
if ! echo "$NEW_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "Error: Invalid version format '$NEW_VERSION'. Expected X.Y.Z"
    exit 1
fi

echo "Bumping version: $CURRENT_VERSION -> $NEW_VERSION"
echo ""

# Check for uncommitted changes
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Warning: You have uncommitted changes."
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Update pyproject.toml
sed -i '' "s/^version = \".*\"/version = \"$NEW_VERSION\"/" pyproject.toml
echo "✓ Updated pyproject.toml"

# Update __init__.py
sed -i '' "s/__version__ = \".*\"/__version__ = \"$NEW_VERSION\"/" src/code_recap/__init__.py
echo "✓ Updated src/code_recap/__init__.py"

# Update uv.lock
if command -v uv &> /dev/null; then
    uv lock --quiet
    echo "✓ Updated uv.lock"
fi

# Show changes
echo ""
echo "Changes:"
git --no-pager diff --color pyproject.toml src/code_recap/__init__.py uv.lock

echo ""
read -p "Commit and tag v$NEW_VERSION? [y/N] " -n 1 -r
echo

if [[ $REPLY =~ ^[Yy]$ ]]; then
    # Check if CHANGELOG.md has [Unreleased] section with content
    if grep -q "## \[Unreleased\]" CHANGELOG.md; then
        UNRELEASED_CONTENT=$(sed -n '/## \[Unreleased\]/,/## \[/p' CHANGELOG.md | grep -E "^### " | head -1)
        if [ -z "$UNRELEASED_CONTENT" ]; then
            echo ""
            echo "⚠️  Warning: CHANGELOG.md [Unreleased] section appears empty."
            read -p "Continue anyway? [y/N] " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                exit 1
            fi
        fi
    else
        echo ""
        echo "⚠️  Warning: CHANGELOG.md doesn't have an [Unreleased] section."
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi

    # Update CHANGELOG.md: rename [Unreleased] to new version with date
    TODAY=$(date +%Y-%m-%d)
    sed -i '' "s/## \[Unreleased\]/## [Unreleased]\n\n## [$NEW_VERSION] - $TODAY/" CHANGELOG.md
    
    # Add new comparison link and update Unreleased link
    sed -i '' "s|\[Unreleased\]: \(.*\)/compare/v.*\.\.\.HEAD|\[Unreleased\]: \1/compare/v$NEW_VERSION...HEAD\n[$NEW_VERSION]: \1/compare/v$CURRENT_VERSION...v$NEW_VERSION|" CHANGELOG.md
    echo "✓ Updated CHANGELOG.md"

    git add pyproject.toml src/code_recap/__init__.py uv.lock CHANGELOG.md
    git commit -m "Bump version to $NEW_VERSION"
    git tag -a "v$NEW_VERSION" -m "Release v$NEW_VERSION"
    
    echo ""
    echo "✓ Created commit and tag v$NEW_VERSION"
    echo ""
    echo "To publish:"
    echo "  git push && git push origin v$NEW_VERSION"
else
    echo ""
    echo "Aborted. Files were modified but not committed."
    echo "To finish manually:"
    echo "  git add pyproject.toml src/code_recap/__init__.py uv.lock"
    echo "  git commit -m 'Bump version to $NEW_VERSION'"
    echo "  git tag -a v$NEW_VERSION -m 'Release v$NEW_VERSION'"
    echo "  git push && git push origin v$NEW_VERSION"
fi
