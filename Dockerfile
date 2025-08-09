FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY tile_proxy.py .
COPY mapdesc.json .

# Expose port
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=tile_proxy.py
ENV FLASK_ENV=production

# Run the application
CMD ["python", "tile_proxy.py"]
