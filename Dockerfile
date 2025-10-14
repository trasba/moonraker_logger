# Dockerfile

# --- Stage 1: Build dependencies with Poetry ---
FROM python:3.11-slim AS builder

# Install poetry
RUN pip install poetry

# Set the working directory
WORKDIR /app

# <<< CHANGE: Tell Poetry to create the virtual env inside the project directory
RUN poetry config virtualenvs.in-project true

# Copy only the dependency files
COPY app/pyproject.toml app/poetry.lock ./

# Install dependencies into the .venv folder within /app
# --no-dev ensures we only install production dependencies
RUN poetry install --no-root


# --- Stage 2: Create the lean runtime image ---
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Copy the virtual environment from the builder stage
# This will now work because the .venv folder exists in /app
COPY --from=builder /app/.venv ./.venv

# Activate the virtual environment by adding it to the PATH
ENV PATH="/app/.venv/bin:$PATH"

# Copy the application code
COPY app/ .

# Define the command to run the application
CMD ["python", "main.py"]