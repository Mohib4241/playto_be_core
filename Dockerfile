FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy project
COPY . /app/

# Create a non-root user and set permissions
RUN useradd -m appuser && \
    chown -R appuser:appuser /app
USER appuser

# Make start.sh executable
# (Note: we do this before switching user or ensure the user has permission)
# Actually, it's better to do it as root then switch.
USER root
RUN chmod +x /app/start.sh
USER appuser

# Expose port
EXPOSE 8000

# Run the start script
CMD ["/app/start.sh"]
