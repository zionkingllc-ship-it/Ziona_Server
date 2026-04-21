#!/bin/bash
# scripts/build_graphql_docs.sh
# Generate the GraphQL schema and build the static SpectaQL documentation

# Ensure we're in the project root
cd "$(dirname "$0")/.." || exit

echo "Generating GraphQL Schema..."
venv/bin/python manage.py shell -c "from strawberry.printer import print_schema; from config.graphql_schema import schema; open('schema.graphql', 'w').write(print_schema(schema))"

echo "Building SpectaQL Documentation..."
npm run docs:build

echo "Documentation generated successfully in docs/graphql-docs/"
