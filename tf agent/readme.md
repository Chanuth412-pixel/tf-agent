# TF-Agentic-Engine

## Overview

**TF-Agentic-Engine** is an autonomous, multi-mode Infrastructure-as-Code (IaC) generation, validation, and optimization pipeline. 

The system programmatically ingests infrastructure states—either discovered from a live AWS deployment via `boto3` or simulated via mock environments—and executes a sequential, multi-agent generation loop using a local Large Language Model (LLM) orchestrated through **LangGraph**. The engine features an automated validation and self-healing loop that compiles, tests, parses, and fixes syntax and reference faults using the native Terraform CLI compiler until a valid state is achieved.

It automatically extracts topological relationships from the configuration, compiles them into a structured graph, and generates visual diagrams (both PNG previews and editable Draw.io HCL diagrams).

---

## Core Architecture

The architecture separates concerns across state management, data discovery, generation domains, and validation systems:

```text
.
├── config/
│   └── settings.py          # LLM context sizes, model parameters, retry policies
├── terraform_workspace/     # Compilation, validation, and asset directory for generated HCL
├── scanner/
│   └── mock_infra.json      # Mock telemetry data for offline local testing
├── src/
│   ├── agent.py             # LangGraph StateGraph compilation and routing logic
│   ├── aws_client.py        # Infrastructure discovery layer & graph compilation
│   ├── nodes.py             # Generation nodes (Network, Security, Compute, Data) & validation node
│   ├── state.py             # GraphState scheme definitions
│   ├── utils.py             # LLM client, HCL correctors, S3 scrubbers, and graph renderers
└── main.py                  # Pipeline execution orchestrator
```

### Module Descriptions

* **`main.py`**: Interacts with the orchestration layer. It initializes states, invokes the environment scan, and triggers the graph execution thread.
* **`src/aws_client.py`**: Connects to AWS APIs to extract topological graphs of resources. For offline test environments, it switches execution to use `moto` simulated backends. It also contains the `compile_infrastructure_graph` utility that translates resource maps into graph nodes and dependency edges.
* **`src/agent.py`**: Implements the directional state transition topology. Configures edge-routing based on compiler validation passes.
* **`src/nodes.py`**: Implements domain-specific code generators. It intercepts input instructions, maps resource groups to isolated configuration layers, injects offline credential configurations, and coordinates visual diagram triggers on successful runs.
* **`src/state.py`**: Controls the `GraphState` thread variables, passing resource configurations, generation histories, topological graphs, compliance reports, and raw compiler error payloads between nodes.
* **`src/utils.py`**: Handles shell sub-processes for `terraform validate` and `terraform fmt`. Features a line-by-line context-aware S3 scrubber, an AST-like RDS subnet group corrector, bracket format protection for LLM template parsing, and rendering engines for visual topology graphs (PNG and Draw.io XML).

---

## Core Features & Recent Milestones

The engine has been upgraded with several advanced features to make the automation loop completely hands-off and bulletproof:

### 1. Expanded Graph State & Telemetry Compiler
* **Topological Relationships**: Discovers and constructs mapping nodes and edges for all network infrastructure components (VPCs, subnets, route tables, gateway attachments, instances, security groups).
* **Relational DB Dependencies**: Captures DB Subnet Groups and maps database instances (`db-master -> aws_db_subnet_group -> subnets`) to ensure databases are deployed cleanly inside subnets.
* **Fallback Resolution**: Automatically injects a DB subnet group node and links it to private subnets if an RDS instance exists without explicit subnet group telemetry in the scan manifest.

### 2. Context-Aware Self-Healing & HCL Firewalls
* **S3 Deprecation Scrubber**: A context-aware line-by-line HCL parser that detects when it is inside an `aws_s3_bucket` block and programmatically removes deprecated properties (`versioning`, `server_side_encryption`, `acl`). This protects the compiler from newer provider version validation crashes while keeping standalone versioning/encryption resource declarations intact.
* **RDS Subnet group corrector**: Automatically detects if an LLM incorrectly attempts to place a list of `subnet_ids` inside an `aws_db_instance` resource. The corrector strips the `subnet_ids` field, writes a separate `aws_db_subnet_group` resource, and links the RDS block using the `db_subnet_group_name` attribute.
* **Strict Prompts & Instructions**: Enforces rules across New, Import, and Clone generation nodes to avoid generating deprecated arguments, using default generic names (e.g. naming database subnet groups `"default"` is strictly forbidden), or inventing ghost subnet IDs that do not exist in the VPC state.

### 3. Automated Validation & Local Credential Mocking
* **Offline Mock Provider Injection**: Before validation executes, `validation_node_func` dynamically injects a local-safe dummy provider block (`provider.tf`) into the workspace. This overrides standard credentials checking and forces the Terraform plan compilation to succeed offline without requiring active AWS keys.
* **Token Protection Firewall**: Escapes curly braces in user prompts and raw JSON structures, substituting them safely inside LangChain templates without throwing parser formatting errors (`unexpected '{' in field name`).

### 4. Interactive Diagram Generation (Visual Assets)
* **PNG Diagram Exporter**: Converts compiled state nodes and edges into an aesthetic Graphviz DOT script, color-codes nodes by layer (VPCs, Subnets, Security Groups, Instances, Databases, Buckets), and renders a physical `architecture.png` topology file.
* **Draw.io XML Layout Generator**: Automatically designs a multi-tier architectural layout and outputs an `architecture.drawio` file. This XML model is fully structured and ready for drag-and-drop adjustments in any standard modeling browser.

---

## Operational Modes

The engine dynamically mutates its prompt constraints and system personas based on the runtime `mode` parameter:

### 1. Import Mode

* **Objective**: Structural mapping of existing telemetry into standard Terraform resources alongside corresponding infrastructure `import` blocks.
* **Constraints**: Enforces strict negative constraints banning the use of input variables (`var.*`). Mandates the use of raw string literals for resource identifiers extracted directly from the AWS state payload.
* **Scope Rule**: Strictly forbids the generation of any resource block missing from the input JSON discovery manifest, suppressing model hallucination behaviors inherited from training data.

### 2. Clone Mode

* **Objective**: Structural parameterization of an un-templated state configuration into clean, reusable modules.
* **Constraints**: Replaces concrete resource keys, names, and environment tags with dynamic `var.*` references.
* **File Structure Enforcement**: Forces the creation of explicit `variable "..." {}` blocks at the top of the output block to maintain self-contained, independent single-file compilation passes.

### 3. New Mode

* **Objective**: Greenfields architectural synthesis derived completely from conversational prompt descriptions.
* **Constraints**: Enforces complete structural literal isolation. Replaces multi-file variable cross-references with direct, hardcoded variable limits to guarantee that dependencies resolve correctly within flat file distributions. Enforces exact explicit naming contracts (`aws_security_group.main`, `aws_vpc.main`) across distinct execution boundaries.

---

## The Self-Healing Runtime Workflow

```text
[Discovery / Prompt Input] 
           │
           ▼
┌──────────────────────────────────────┐
│  GraphState Initialization           │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Network Node Generation             │──► Emits network.tf
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Security Node Generation            │──► Emits security.tf
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Compute Node Generation             │──► Emits compute.tf
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Data Node Generation                │──► Emits data.tf
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Validation Node (Subprocess Match)  │
└──────────┬───────────────────────────┘
           │
           ├─── [Exit Code != 0] ──► [Sanitize Error Logs] ──► [HCL Self-Healing Correctors]
           │                                                               │
           │                                                               ▼
           │                                                  (Rewinds to Network Node)
           │
           └─── [Exit Code == 0] ──► [Validation Passed: True]
                                               │
                                               ▼
                                  ┌─────────────────────────┐
                                  │   Extract Graph State   │
                                  └────────────┬────────────┘
                                               │
                                               ▼
                                  ┌─────────────────────────┐
                                  │ Visual Assets Render    │
                                  │ Emits:                  │
                                  │  - architecture.png     │
                                  │  - architecture.drawio  │
                                  └─────────────────────────┘
```

1. **Context Window Protection**: To defeat context bleed and prompt pollution on local open-weight architectures, the engine modifies the prompt array before execution. In `import` and `clone` modes, user-facing prompt strings are omitted, forcing the attention layer to prioritize raw JSON payload arrays.
2. **Domain Boundaries**: Nodes use strict domain filters to ensure they only manage resources inside their lane. For example, the Data node explicitly drops networking components to prevent role conflicts across files.
3. **Subprocess Compilation Tracking**: The Validation node executes `terraform validate -json` or raw log capturing via an underlying shell engine.
4. **Token & Template Protection**: Raw compiler logs containing literal structural brackets (`{}`) are dynamically escaped via string formatting functions, transforming them into `{{` and `}}` chains before they are sent to the error log. This prevents LangChain template rendering exceptions.
5. **Feedback Loop Reinforcement**: When a compiler failure is caught, the full error array is pushed into the state history. The engine prefixes standard instructions to the error log, explicitly commanding the LLM to drop non-compliant blocks instead of generating more code to fix hallucinated dependencies.

---

## Installation & Configuration

### 1. Environment Preparation

Install the minimum required package configurations:

```bash
pip install -r requirements.txt
```

Ensure the local environment is configured with a functional local Terraform binary and proper cache definitions. For offline configurations, map your local mirror using a `.terraformrc` layout configuration:

```bash
export TF_CLI_CONFIG_FILE="../.terraformrc"
```

### 2. LLM Orchestration Configuration

Verify the local Ollama daemon status and select a model with appropriate precision parameters. For complex structural generation tasks, a 70B parameter architecture or a specialized code-tuned architecture is required.

Pull the model execution target:

```bash
ollama pull llama3:70b
```

Update system bounds inside `config/settings.py`:

```python
# config/settings.py

MODEL_NAME = "llama3:70b"
OLLAMA_BASE_URL = "http://localhost:11434"
NUM_CTX = 8192  # Expanded token window required to prevent topology truncation
MAX_RETRY_COUNT = 3
DEBUG = False  # Toggle this to True/False to enable/disable debug messages (can also be set via DEBUG environment variable)
```

---

## Execution Interface

### 1. Workspace Isolation

Purge previous deployment runs before spinning up a validation pass:

```bash
rm -f terraform_workspace/*.tf
```

### 2. Running Pipeline Operations

Set execution conditions within `main.py` to target a specific profile state:

```python
# Example initialization context inside main.py
initial_state = {
    "mode": "new",
    "aws_input_data": {},
    "user_prompt": "Generate a scalable AWS infrastructure for a mobile application backend. Include an AWS Cognito User Pool for mobile authentication, an API Gateway for mobile client requests, AWS Lambda functions for backend logic, and a DynamoDB table for storing user data."
}
```

Trigger the execution engine:

```bash
python3 main.py
```

The system will cycle across structural evaluation loops, feeding compiler faults directly back into generation layers until `Validation Passed: True` is captured at the validation node boundary, automatically producing the sanitized HCL blocks and visual diagram files inside `terraform_workspace/`.