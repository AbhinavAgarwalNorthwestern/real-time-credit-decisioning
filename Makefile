
dev:
	uv run services/${service}/src/${service}/main.py

build-and-push:
	./scripts/build-and-push-image.sh ${image} ${env}

# run:build-for-dev
# 	docker run -it ${service}:dev
deploy:
	./scripts/deploy.sh ${service} ${env}

lint:
	ruff check . --fix
