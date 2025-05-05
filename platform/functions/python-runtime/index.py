print("SST DEV PYTHON RUNTIME: custom build 2024-06-XX", flush=True)
import importlib
import json
import os
import sys
import traceback
import time
import requests
import re


# Error handling function to report errors back to the Lambda runtime API
def report_error(ex, context=None):
    error_response = {
        "errorType": "Error",
        "errorMessage": str(ex),
        "trace": traceback.format_exc().split("\n"),
    }

    endpoint = (
        f"{AWS_LAMBDA_RUNTIME_API}/runtime/init/error"
        if context is None
        else f"{AWS_LAMBDA_RUNTIME_API}/runtime/invocation/{context['awsRequestId']}/error"
    )
    requests.post(
        endpoint,
        headers={"Content-Type": "application/json"},
        data=json.dumps(error_response),
    )


def log(message):
    print(message, flush=True)
    sys.stdout.flush()
    sys.stderr.flush()


def find_project_root(start_path):
    """Find the Python project root by looking for project files."""
    current = os.path.abspath(start_path)
    while current != os.path.dirname(current):  # Stop at root directory
        if any(os.path.exists(os.path.join(current, f)) 
               for f in ['pyproject.toml', 'setup.py', 'requirements.txt']):
            return current
        current = os.path.dirname(current)
    return os.getcwd()  # Fallback to current directory


def setup_python_path(handler_path):
    """Set up Python path to handle nested package imports correctly."""
    log(f"Setting up Python path for handler: {handler_path}")
    
    # Split handler path (e.g., "src.lambda_handlers.api.handler" or "src/lambda_handlers/api.handler")
    if "." in handler_path:
        module_path, function_name = handler_path.rsplit(".", 1)
        # Convert dots to system path separator for finding the file
        file_path = module_path.replace(".", os.path.sep)
    else:
        raise ValueError(f"Invalid handler format: {handler_path}")
    
    # Find the project root
    project_root = find_project_root(os.getcwd())
    log(f"Project root found at: {project_root}")
    
    # Add paths to sys.path in order of precedence
    paths_to_add = [
        project_root,  # Project root for package imports
        os.path.dirname(project_root),  # Parent of project root
        os.getcwd(),  # Current working directory
    ]
    
    # Add unique paths in reverse order (most specific first)
    for path in reversed(paths_to_add):
        if path not in sys.path:
            sys.path.insert(0, path)
            log(f"Added to Python path: {path}")
    
    return module_path, function_name


# Parse the handler from command-line arguments
handler = sys.argv[1]  # Expecting the format 'module.function' or 'path/to/module.function'
AWS_LAMBDA_RUNTIME_API = f"http://{os.environ['AWS_LAMBDA_RUNTIME_API']}/2018-06-01"

def parse_handler(handler_str):
    """Parse the handler string into module path and function name."""
    if "." not in handler_str:
        raise ValueError("Handler must be in the form 'module.function'")
    
    # Convert any path separators to dots for Python importing
    normalized_handler = handler_str.replace("/", ".").replace("\\", ".")
    module_path, function_name = normalized_handler.rsplit(".", 1)
    
    log(f"Parsed handler - module: {module_path}, function: {function_name}")
    return module_path, function_name

try:
    module_path, function_name = setup_python_path(handler)
    log(f"Attempting to import {module_path}.{function_name}")
    
    module = importlib.import_module(module_path)
    handler_function = getattr(module, function_name)
    
    if not callable(handler_function):
        raise ImportError(f"{function_name} is not a callable function in {module_path}")
        
    log(f"Successfully imported handler function")
except Exception as ex:
    log(f"Import error: {ex}")
    log(f"Current sys.path: {sys.path}")
    report_error(ex)
    sys.exit(1)

# Simulating Lambda's event loop
while True:
    try:
        # Get the next event to process
        response = requests.get(f"{AWS_LAMBDA_RUNTIME_API}/runtime/invocation/next")
        response.raise_for_status()

        context = {
            "awsRequestId": response.headers.get("Lambda-Runtime-Aws-Request-Id"),
            "invokedFunctionArn": response.headers.get(
                "Lambda-Runtime-Invoked-Function-Arn"
            ),
            "getRemainingTimeInMillis": lambda: max(
                int(response.headers.get("Lambda-Runtime-Deadline-Ms"))
                - int(time.time() * 1000),
                0,
            ),
            "functionName": os.environ.get("AWS_LAMBDA_FUNCTION_NAME"),
            "functionVersion": os.environ.get("AWS_LAMBDA_FUNCTION_VERSION"),
            "memoryLimitInMB": os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE"),
            "logGroupName": os.environ.get("AWS_LAMBDA_LOG_GROUP_NAME"),
            "logStreamName": os.environ.get("AWS_LAMBDA_LOG_STREAM_NAME"),
        }

        event = response.json()

    except Exception as ex:
        log(f"Error getting next invocation: {ex}")
        report_error(ex)
        continue

    # Run the handler function
    try:
        result = handler_function(event, context)
    except Exception as ex:
        log(f"Error running handler: {ex}")
        report_error(ex, context)
        continue

    # Send the response back to Lambda
    while True:
        try:
            requests.post(
                f"{AWS_LAMBDA_RUNTIME_API}/runtime/invocation/{context['awsRequestId']}/response",
                headers={"Content-Type": "application/json"},
                data=json.dumps(result),
            )
            break
        except Exception as _:
            time.sleep(0.5)
            continue

    sys.stdout.flush()
    sys.stderr.flush()
