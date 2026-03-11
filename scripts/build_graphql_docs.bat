@echo off
REM scripts\build_graphql_docs.bat
REM Generate the GraphQL schema and build the static SpectaQL documentation

REM Ensure we're in the project root
cd %~dp0..

echo Generating GraphQL Schema...
venv_win\Scripts\python manage.py shell -c "from strawberry.printer import print_schema; from config.graphql_schema import schema; open('schema.graphql', 'w').write(print_schema(schema))"

echo Building SpectaQL Documentation...
call npm run docs:build

echo Documentation generated successfully in docs\graphql-docs\
