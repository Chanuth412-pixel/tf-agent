# TF-Agentic-Engine

##  Overview

**TF-Agentic-Engine** is an autonomous Infrastructure-as-Code generator.

It dynamically scans your live AWS environment, or a simulated local environment, to discover existing resources. Then it uses a local Large Language Model through **LangGraph** to generate modular Terraform HCL code.

The engine also includes a self-healing loop. It validates the generated Terraform code using the Terraform CLI and retries automatically until the code becomes valid or the iteration limit is reached.

---

##  Core Architecture

The project follows a clean, modular structure:

- **`main.py`** - Entry point. Starts the AWS fetcher and runs the LangGraph workflow.
- **`src/aws_client.py`** - AWS discovery module. Uses `boto3` to fetch AWS resources and `moto` for safe local testing.
- **`src/agent.py`** - Builds and compiles the LangGraph `StateGraph`.
- **`src/nodes.py`** - Contains generation nodes for Network, Security, Compute, Data, and Validation.
- **`src/state.py`** - Defines `GraphState` and the initial state structure.
- **`src/utils.py`** - Contains prompts, LLM client logic, HCL cleanup, file writing, and Terraform validation.
- **`config/settings.py`** - Stores configuration such as model name, Ollama URL, context size, and max iterations.

---

##  Runtime Flow

1. **Discover** - Fetch the current AWS infrastructure state using `boto3`.
2. **Initialize** - Add the discovered AWS data into the LangGraph state.
3. **Generate** - Run the generation nodes in order: **Network → Security → Compute → Data**.
4. **Write** - Save generated Terraform files inside `terraform_workspace/`.
5. **Validate** - Run `terraform fmt` to check formatting and structure.
6. **Self-Correct** - If errors exist, route back to the correct node and retry until the code is valid or the limit is reached.

---

##  Quick Start Guide

Follow these steps to set up and run the pipeline.

### 1. Install Dependencies

Install the required Python packages:

```bash
pip install -r requirements.txt
```

---

### 2. Configure AWS

The system supports two modes:

#### Live AWS Mode

Use this mode if you want to scan your real AWS account.

```bash
aws configure
```

Make sure the IAM user has read-only access, for example:

```text
ec2:Describe*
s3:List*
```

#### Local Testing Mode

Use this mode if you do not have AWS credentials.

The system uses `moto` inside `src/aws_client.py` to safely mock AWS resources in your computer's memory. This does not create real AWS resources or charges.

---

### 3. Verify Ollama Status

Make sure Ollama is running, then pull the model you want to use:

```bash
ollama pull llama3:70b
```

You can also use a smaller model depending on your hardware:

```bash
ollama pull qwen2.5:7b
```

or:

```bash
ollama pull llama3.2
```

---

### 4. Update Configuration

Open:

```text
config/settings.py
```

Check that these values match your environment:

```python
MODEL_NAME = "llama3:70b"
NUM_CTX = 4096
```

For larger AWS payloads, you can use:

```python
NUM_CTX = 8192
```

---

### 5. Clean the Workspace

Remove old Terraform files before running a new generation:

```bash
rm -f terraform_workspace/*.tf
```

---

### 6. Run the Pipeline

Start the main application:

```bash
python3 main.py
```

The system will discover infrastructure, generate Terraform files, validate them, and retry automatically if needed.