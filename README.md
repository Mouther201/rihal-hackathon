# SitShift - Smart Office Seating Planner

SitShift is a modern, intelligent office seating planner that optimizes team collaboration and workspace utilization. It creates strategic seating arrangements based on departmental needs and supports hybrid working patterns.

![SitShift Logo](static/logo.gif)

## Features

- **Smart Assignment**: Intelligently assigns seats to optimize workspace utilization and team collaboration
- **Department Grouping**: Keeps teams together to enhance communication and productivity
- **Calendar Integration**: Seamlessly integrates seating schedules with Google Calendar
- **Interactive Visualization**: Provides clear visual floor plans of the seating arrangements
- **CSV Data Import**: Easily upload employee data in CSV format
- **Modern Interface**: Clean, responsive design that works on various devices

## Installation

### Prerequisites

- Python 3.8 or higher
- pip (Python package installer)

### Step 1: Clone the repository

```bash
git clone https://github.com/yourusername/seating-planner.git
cd seating-planner
```

### Step 2: Set up a virtual environment (optional but recommended)

```bash
# For Windows
python -m venv venv
venv\Scripts\activate

# For macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install dependencies

```bash
pip install -r requirements.txt
```

If you don't have a `requirements.txt` file, you can install the required packages manually:

```bash
pip install fastapi uvicorn pandas numpy ortools plotly python-multipart jinja2
```

## Running the Application

### Start the server

```bash
python main.py
```

The application will start and be available at http://localhost:8000

### Using Docker (optional)

If you prefer using Docker, you can build and run a container:

```bash
# Build the Docker image
docker build -t seating-planner .

# Run the container
docker run -p 8000:8000 seating-planner
```

## Usage Guide

### 1. Prepare Your Data

Create a CSV file with the following columns:
- `ID`: Unique identifier for each employee
- `Department`: Department code/name for each employee

Example:
```
ID,Department
101,HR
102,IT
103,HR
104,SALES
...
```

### 2. Upload Data

1. Open the application in your web browser (http://localhost:8000)
2. Drag and drop your CSV file onto the upload area or click to browse and select the file
3. Wait for the processing to complete

### 3. View Results

After processing, you'll see:
- Floor plans with assigned seating
- Weekly attendance calendar showing employee distribution
- Summary statistics for the seating plan

### 4. Download & Calendar Integration

- Click "Download Seating Plan" to download the CSV file with assignments
- Click "Save to Google Calendar" to add the schedule to your calendar
  - You can choose to add individual days or all days at once

## Algorithm Details

SitShift uses constraint programming (Google OR-Tools) to generate optimal seating arrangements based on:
- Department grouping (team members are seated on the same floor)
- Floor capacity constraints
- Maximum percentage of department members on-site
- Seat availability
- Equitable distribution of staff

## Development

### Project Structure

- `main.py`: FastAPI application and main logic
- `static/`: Static assets and frontend files
  - `index.html`: Main web interface
  - `styles.css`: CSS styling
  - `logo.gif`: Application logo
- `uploads/`: Temporary storage for uploaded files
- `processed/`: Processed output files

### Extending the Application

To add new features or modify the existing ones:

1. Backend changes: Update the FastAPI endpoints in `main.py`
2. Frontend changes: Modify `static/index.html` and `static/styles.css`
3. Algorithm changes: Update the constraint model in the `solve_seating()` function

## Troubleshooting

### Common Issues

- **"Error processing file"**: Ensure your CSV has the correct columns (ID, Department)
- **Visualization not showing**: Check browser console for JavaScript errors
- **Calendar integration not working**: Make sure pop-up blockers are disabled
- **OR-Tools installation issues**: See [OR-Tools documentation](https://developers.google.com/optimization/install)

## License

[MIT License](LICENSE)

## Contact

For support or inquiries, please contact [malhashmi001@gmail.com](mailto:malhashmi001@gmail.com)

## Docker Setup

This project is containerized with Docker for easy deployment and consistent development environments. Below are the three primary Docker commands demonstrated in this project:

### 1. Docker Build

Build a Docker image from the Dockerfile:

```bash
docker build -t sitshift .
```

This command creates a Docker image named "sitshift" based on the instructions in the Dockerfile.

### 2. Docker Run

Run a container from the built image:

```bash
docker run -p 8080:5000 sitshift
```

This command starts a container from the "sitshift" image, mapping port 5000 inside the container to port 8080 on your host machine.

### 3. Docker Compose

The preferred way to run this application:

```bash
# Start the services
docker-compose up

# Stop and remove the services
docker-compose down
```

`docker-compose up` builds the image if needed, starts the container, and streams logs to the console. The API becomes reachable at http://localhost:8080.

`docker-compose down` cleanly stops and removes the container along with any anonymous volumes.

### 4. Pushing to Docker Hub

To push the Docker image to Docker Hub for sharing with others, follow these steps:

1. Make sure you're logged in to Docker Hub:
   ```bash
   docker login
   ```

2. Build the image first, then push it:
   ```bash
   # Build the image
   docker-compose build
   
   # Push the image
   docker-compose push
   ```

For convenience, you can use the provided scripts that handle both building and pushing:

- **Windows**: Run `build-and-push.bat`
- **macOS/Linux**: Run `./build-and-push.sh` (make it executable with `chmod +x build-and-push.sh`)

## Application Access

After starting the application using either `docker run` or `docker-compose up`, access:

- Web Interface: http://localhost:8080
- Health Check API: http://localhost:8080/health (returns `{"message": "Todo service ready"}`)

## Prerequisites

- Docker and Docker Compose

No other installations required. The application has been verified to run identically on Windows, macOS, and Linux.
