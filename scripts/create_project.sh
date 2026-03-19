#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: bash scripts/create_project.sh <project-slug> [destination-root]" >&2
  exit 1
fi

project_slug="$1"
destination_root="${2:-}"

if [[ ! "$project_slug" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "Project slug must match ^[a-z0-9][a-z0-9-]*$" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "$0")" && pwd)"
source_root="$(cd "$script_dir/.." && pwd)"

if [ -z "$destination_root" ]; then
  destination_root="$source_root"
fi

template_root="$source_root/projects/_template"
project_root="$destination_root/projects/$project_slug"

mkdir -p "$destination_root/projects"

if [ ! -d "$template_root" ]; then
  echo "Project template not found: $template_root" >&2
  exit 1
fi

if [ ! -d "$source_root/_system/templates" ]; then
  echo "System templates not found in: $source_root/_system/templates" >&2
  exit 1
fi

if [ -e "$project_root" ]; then
  echo "Project already exists: $project_root" >&2
  exit 1
fi

cp -R "$template_root" "$project_root"

while IFS= read -r file_path; do
  tmp_path="${file_path}.tmp"
  sed "s|{{PROJECT_SLUG}}|$project_slug|g" "$file_path" > "$tmp_path"
  mv "$tmp_path" "$file_path"
done < <(find "$project_root" -type f ! -name '.gitkeep' | sort)

printf 'Created project scaffold: %s\n' "$project_root"
