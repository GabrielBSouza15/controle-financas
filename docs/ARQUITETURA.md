# Organização técnica

- `app/domain`: dinheiro, parcelas, datas e regras financeiras puras.
- `app/persistence`: modelos relacionais, transações e consultas SQLite/SQLAlchemy.
- `app/services`: operações financeiras, projeções e backups.
- `app/api`: validação das entradas, rotas FastAPI e proteção local.
- `app/templates` e `app/static`: interface Jinja2, HTML, CSS, JavaScript e SVG.
- `migrations`: criação e evolução do esquema via Alembic.
- `scripts/prepare_database.py`: preparação independente de banco vazio ou didático.
- `tests`: cenários automatizados com dados de teste e bancos temporários.

Valores monetários usam centavos inteiros; conversões decimais usam Decimal. Operações preservam vínculos e histórico, com chaves de idempotência para evitar repetição de efeitos. Previsões, compromissos e pagamentos têm papéis distintos. O navegador apresenta os totais calculados no backend.

A aplicação usa recursos locais, validação de origem e token CSRF para escritas. O uso de IA foi apoio ao desenvolvimento e ao estudo; a aplicação não depende de uma API de IA para funcionar.
