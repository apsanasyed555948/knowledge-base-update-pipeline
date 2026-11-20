
# Real-Time Gen AI Customer Service Bot
## Complaint Triage and Escalation System

### Features
- Sentiment and frustration analysis
- Urgency and sarcasm detection
- High-risk complaint escalation
- After-hours complaint scheduling
- Automatic escalation after 15 minutes
- Escalation audit logging

### Requirements
- Python 3.10 or later

### Run
```bash
python complaint_triage.py
python escalation_scheduler.py
```

### Output
Displays complaint analysis, escalation decisions, queues, and audit logs.



# Multilingual RAG Knowledge Base Pipeline

## Project Overview
The Multilingual RAG Knowledge Base Pipeline is a Python-based project that manages knowledge-base documents and provides reliable answers to user questions using Retrieval-Augmented Generation (RAG).

The project extends the training project with document management, multilingual question answering, security, quality evaluation, version control, and deployment features.

## Key Features

- **Document Processing:** Detects new, modified, unchanged, duplicate, and invalid documents.
- **RAG-Based Question Answering:** Retrieves relevant knowledge-base information to answer user questions.
- **Multilingual Support:** Supports English, Hindi, Telugu, and Tamil policy queries.
- **Conversation Management:** Provides configurable language settings, context limits, and session settings.
- **Version Management:** Maintains document versions and supports rollback.
- **Quality Evaluation:** Checks answer accuracy and grounding before deployment.
- **Security:** Includes role-based access control, prompt-injection detection, and sensitive-data masking.
- **Deployment Management:** Supports scheduled deployment, maintenance windows, health checks, and rollback.
- **Monitoring:** Tracks latency, failures, confidence, and escalation rates.
- **PDF Processing:** Includes PDF validation and text extraction functionality.

## Technologies Used

- Python
- Retrieval-Augmented Generation (RAG)
- JSON
- Streamlit (where applicable)
- PDF processing libraries

## Project Structure

```text
knowledge_base_pipeline/
│
├── config/
├── documents/
├── logs/
├── quarantine/
├── tests/
├── versions/
│
├── conversation_manager.py
├── document_processor.py
├── rag_assistant.py
├── multimodal_processor.py
├── security_manager.py
├── quality_checker.py
├── version_manager.py
├── deployment_manager.py
├── monitoring.py
└── test_rag_assistant.py
```

## How to Run

1. Clone the repository.
2. Open the project folder.
3. Install the required Python dependencies.
4. Configure the settings in `config/config.json`.
5. Add knowledge-base documents to the `documents` folder.
6. Run the document processor:

```bash
python document_processor.py
```

7. Run the RAG assistant using its available interface or module.
8. Run the tests:

```bash
python -m unittest -v test_rag_assistant.py
```

## Testing

The project includes tests for:
- Current and historical policy retrieval.
- Multilingual policy responses.
- Customer and staff access restrictions.
- Invalid user roles.
- Prompt-injection protection.

Six RAG unit tests have passed during development.

## Conclusion

This project demonstrates how RAG, multilingual conversation handling, document management, security, quality evaluation, and deployment monitoring can be combined to build a reliable knowledge-base chatbot.


# Support Ticket Workflow – Complaint Triage System

## Project Overview

The Support Ticket Workflow is a Python-based complaint management system developed using Streamlit. It helps analyze customer complaints, identify sentiment, assign priority levels, and manage ticket escalation.

The system improves customer support by organizing complaints and identifying urgent issues that require attention.

## Key Features

- **Sentiment Analysis:** Identifies positive, negative, neutral, frustrated, urgent, and sarcastic customer messages.
- **Complaint Classification:** Analyzes customer complaints and categorizes them based on their content.
- **Priority Management:** Assigns priority to complaints based on sentiment and risk.
- **Risk Detection:** Identifies important risk categories such as account compromise, duplicate payment, and legal threats.
- **Priority Risk Queue:** Organizes high-risk complaints for faster attention.
- **Automatic Escalation:** Escalates unresolved negative complaints after 15 minutes.
- **Business Hours Configuration:** Supports Monday to Friday, 9:00 AM to 6:00 PM.
- **Timezone Support:** Uses Asia/Kolkata timezone.
- **Ticket Management:** Helps organize and track customer complaints.
- **Streamlit Interface:** Provides a simple interface for interacting with the complaint management system.

## Technologies Used

- Python
- Streamlit
- JSON
- Date and Time Handling
- Sentiment Analysis
- Rule-Based Complaint Classification

## Project Structure

```text
support_ticket_workflow/
│
├── app.py
├── config/
├── data/
├── logs/
├── tests/
├── requirements.txt
└── README.md
```

*Note: Update the project structure above to match the actual filenames and folders in your repository.*

## How to Run

1. Clone the repository.
2. Open the project folder.
3. Install the required dependencies:

```bash
pip install -r requirements.txt
```

4. Start the Streamlit application:

```bash
streamlit run app.py
```

5. Open the local URL displayed in the terminal.

## Complaint Escalation Rules

- Negative complaints that remain unresolved for more than 15 minutes are escalated.
- Account compromise complaints are identified as high-risk.
- Duplicate payment complaints are flagged for review.
- Legal threats are identified for priority attention.
- Business hours are configured from Monday to Friday, 9 AM to 6 PM.

## Expected Benefits

- Faster identification of urgent complaints.
- Better organization of customer support tickets.
- Improved complaint prioritization.
- Automatic escalation of unresolved complaints.
- More efficient customer support workflow.

## Conclusion

The Support Ticket Workflow simplifies complaint management through sentiment detection, risk identification, priority classification, and automatic escalation. It helps support teams handle customer complaints in an organized and efficient manner.
