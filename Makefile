.PHONY: up test dev-api dev-web
up:
	docker compose up --build
test:
	cd apps/api && python -m pytest tests -q
dev-api:
	cd apps/api && uvicorn app.main:app --reload
dev-web:
	pnpm --dir apps/web dev
