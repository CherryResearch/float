# Variables
ENV=.venv
FRONTEND_DIR=frontend

# Targets
.PHONY: setup run build start run-frontend clean test all

setup:
	# Set up the backend and root frontend workspace
	poetry install
	npm install

run:
	# Launch the backend and frontend with Float's shared port/runtime setup
	poetry run float

build:
	# Build frontend
	npm run frontend:build

start: run

run-frontend:
	# Run frontend
	npm run frontend:dev

clean:
	# Clean backend and frontend artifacts
	rm -rf $(ENV)
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -delete
	cd $(FRONTEND_DIR) && rm -rf node_modules dist

test:
	# Run all backend test roots configured in pytest.ini
	poetry run pytest

all:
	# Full setup and run
	make setup && make start

#make setup   # Sets up the environment
#make run     # Launches the app
#make build   # Builds frontend assets
#make start   # Runs both backend and frontend
#make clean   # Cleans up build artifacts
