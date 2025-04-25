# Use the pre-built image optimized for Selenium on Lambda
# Source: https://github.com/umihico/docker-selenium-lambda
FROM umihico/aws-lambda-selenium-python:latest

# Set the working directory
WORKDIR /app

# Install Python dependencies from requirements.txt
COPY requirements.txt .
# Ensure pip is up-to-date within the base image if necessary (often good practice)
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# The base image likely sets the appropriate CMD, but we specify ours based on app.py
# AWS Lambda will look for 'app.handler'
CMD ["app.handler"] 