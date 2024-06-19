"""
This action package is used by the Runbook Tutor to retrieve available actions from the Sema4
Desktop action servers. The returned actions will include their names and descriptions, but will
exclude actions used by the Runbook Tutor itself.
"""

import json
import os
import mimetypes
import requests
from pathlib import Path
from typing import Annotated, Any, Dict
from datetime import datetime
import yaml

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from sema4ai.actions import action

ACTION_ROOT = Path(__file__).parent
DEVDATA = ACTION_ROOT / "devdata"
TEMPLATE = ACTION_ROOT / "template.yml"


class ActionPackage(BaseModel):
    name: Annotated[str, Field(description="The name of the action.")]
    port: Annotated[int, Field(description="The port the action server is running on.")]
    api_spec: Annotated[
        dict,
        Field(
            description="The API specification of the action "
            "retrieved from the action server."
        ),
    ]


class ActionPackages(BaseModel):
    actions: Annotated[
        list[ActionPackage],
        Field(
            description="A list of actions available on the Sema4 Desktop action servers."
        ),
    ]


class InternalActionPackages(BaseModel):
    names: Annotated[list[str], Field(description="The names of the internal actions.")]


HARDCODED_INTERNAL_ACTIONS = InternalActionPackages(
    names=[
        "Sema4 Desktop Action Getter",
        "Thread Monitor",
        "Agent Deployer",
        "Retreival",
    ]
)


@action
def get_actions(internal_actions: InternalActionPackages) -> ActionPackages:
    """
    Retrieve available actions from the Sema4 Desktop action servers. The returned actions will
    include their names, descriptns and full OpenAI tool specification. You can exclude
    certain actions from the return by passing them as internal actions.

    Args:
        internal_actions: A list of actions to exclude from the return.

    Returns:
        A list of actions available on the Sema4 Desktop action servers.
    """
    load_dotenv(DEVDATA / ".env")
    ROBOCORP_HOME = os.environ["ROBOCORP_HOME"]
    SEMA4_DESKTOPHOME = f"{ROBOCORP_HOME}/sema4ai-desktop"

    with open(f"{SEMA4_DESKTOPHOME}/config.json") as f:
        config = json.loads(f.read())
    actions = []
    for action_mapping in config["ActionPackageMapping"]:
        action_path = action_mapping["path"]
        with open(f"{action_path}/metadata.json") as f:
            api_spec = json.loads(f.read())
        if action_mapping["name"] in internal_actions.names:
            continue
        actions.append(
            ActionPackage(
                name=action_mapping["name"],
                port=action_mapping["actionServerPort"],
                api_spec=api_spec,
            )
        )
    return ActionPackages(actions=actions)


def handle_relative_file_path(file_path: str) -> Path:
    path = Path(file_path)
    if not path.is_absolute():
        return ACTION_ROOT / file_path
    return path


# Load the YAML file
def load_yaml_file(file_path: str) -> Dict[str, Any]:
    print(f"Loading Agent Runtime Bundle: {file_path}")
    with open(handle_relative_file_path(file_path), "r") as file:
        data = yaml.safe_load(file)  # type: Dict[str, Any]
    return data


def read_binary_file(file_path: str) -> bytes:
    with open(
        handle_relative_file_path(file_path), "rb"
    ) as file:  # Note the 'rb' mode for binary files
        content = file.read()  # Read the entire file into a bytes object
    return content


def read_text_file(file_path: str) -> str:
    with open(handle_relative_file_path(file_path), "r") as file:
        content = file.read()  # Read the entire file into a single string
    return content


def get_mime_type(file_path: str) -> str:
    if file_path.endswith(".md"):
        return "text/plain"
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type if mime_type is not None else "application/octet-stream"


def deploy_agent(agent: dict) -> str:
    print(f"Deploying agent: {agent['name']}")

    print(f"Loading runbook/system prompt: {agent['system-prompt']}")
    try:
        system_prompt = read_text_file(agent["system-prompt"])
    except (FileNotFoundError, OSError):
        system_prompt = agent["system-prompt"]

    print(f"Loading retrieval prompt: {agent['retrieval-prompt']}")
    retrieval_prompt = read_text_file(agent["retrieval-prompt"])

    tools = []
    if "tools" in agent:
        for t in agent["tools"]:
            print(f"Adding tool: {t}")
            if isinstance(t, dict):
                tools.append(t)
                continue
            tools.append({"config": {"name": t.title()}, "type": t, "name": t.title()})

    jsn = {
        "name": agent["name"],
        "config": {
            "configurable": {
                "type==agent/retrieval_description": retrieval_prompt,
                "type==agent/agent_type": agent["model"],
                "type==agent/system_message": system_prompt,
                "type==agent/tools": tools,
                "type": "agent",
                "type==agent/interrupt_before_action": False,
                "type==agent/description": agent["description"],
            }
        },
    }

    resp = requests.post("http://localhost:8100/assistants", json=jsn)
    assistant = json.loads(resp.content)
    assistant_id = assistant["assistant_id"]
    print(resp.content)
    # print(assistant)

    if "files" in agent:
        print(f"Uploading files for agent: {agent['name']}")

        for file_path in agent["files"]:
            # Get the filename
            filename = os.path.basename(file_path)
            print(f"Uploading file: {filename}")
            # Guess the MIME type of the file or use 'application/octet-stream' if unknown
            mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            # Open the file in binary mode and add to the files dictionary
            files = {"files": (filename, open(file_path, "rb"), mime_type)}

            config = {
                "configurable": {
                    # RAG files can be attached to thread or assistants, but not both
                    # 'thread_id': thread['thread_id'],
                    "assistant_id": assistant_id,
                }
            }

            config = {"config": json.dumps(config)}

            response = requests.post(
                "http://localhost:8100/ingest",
                files=files,
                data=config,
                headers={"accept": "application/json"},
            )
            print(response.content)

    jsn = {
        "name": "Welcome",
        "assistant_id": assistant_id,
        "starting_message": "Hi! How can I help you with today?",
    }
    resp = requests.post("http://localhost:8100/threads", json=jsn)
    print(resp.content)
    thread_id = json.loads(resp.content)["thread_id"]

    return assistant_id, thread_id


def create_action_server_config(action_name: str, port: int) -> dict:
    """
    Create the config for a tool that is a action server.
    """
    return {
        "type": "action_server_by_sema4ai",
        "name": "Action Server by Sema4.ai",
        "description": "Run AI actions with [Sema4.ai Action Server](https://github.com/Sema4AI/actions).",
        "config": {
            "url": f"http://localhost:{port}",
            "api_key": "APIKEY",
            "name": action_name,
            "isBundled": "false",
        },
    }


@action
def deploy_agent_to_desktop(
    name: str, description: str, system_prompt: str, tool_names: str
) -> str:
    """
    Deploys an agent to desktop that will use the provided system prompt as it's Runbook.

    Args:
        name: The name of the agent to deploy.
        description: The description of the agent to deploy.
        system_prompt: The system prompt to use for the agent.
        tool_names: The names of the tools to use for the agent as a JSON string representation of
            a list of dictionaries, see example below. The port number MUST be obtained from the
            Action Getter tool and passed in as an integer.

            ```
            [
                {
                    "tool_name": "Dummy Tool",
                    "port": port-as-int
                },
                {
                    ...
                }
            ]
            ```

    Returns:
        The assistant ID of the deployed agent.
    """
    bundle = load_yaml_file(TEMPLATE)["s4d-bundle"]
    agent_to_deploy = bundle["agents"][0]["agent"]
    agent_to_deploy["name"] = name
    agent_to_deploy["description"] = description
    agent_to_deploy["system-prompt"] = system_prompt
    tools = []
    for tool in json.loads(tool_names):
        tools.append(create_action_server_config(tool["tool_name"], tool["port"]))
    agent_to_deploy["tools"] = tools
    assistant_id, thread_id = deploy_agent(agent_to_deploy)

    out = {
        "assistant_id": assistant_id,
        "thread_id": thread_id,
    }
    return repr(out)

@action
def get_latest_thread(assistant_id: str) -> str:
    """
    Gets the latest thread of an agent.

    Args:
        assistant_id: Id of the assistant.

    Returns:
        The content of the thread.
    """

    resp = requests.get('http://127.0.0.1:8100/threads/')
    threads = resp.json()

    # Filter threads for the given assistant
    assistant_threads = [thread for thread in threads if thread['assistant_id'] == assistant_id]

    if assistant_threads:
        # Convert the 'updated_at' string to a datetime object for comparison
        for thread in assistant_threads:
            thread['updated_at'] = datetime.fromisoformat(thread['updated_at'].replace('Z', '+00:00'))

        # Find the thread with the latest 'updated_at' timestamp
        latest_thread = max(assistant_threads, key=lambda thread: thread['updated_at'])

        thread_id = latest_thread['thread_id']
        print(f"Thread we are looking at is: {thread_id}")

        resp = requests.get(f'http://127.0.0.1:8100/threads/{thread_id}/history')
        json_data = json.loads(resp.content)

        # Extract messages
        messages = json_data[0]['values']['messages']
        # Initialize summary string
        summary = []

        print(messages)

        # Process messages
        for message in messages:
            if message['type'] == 'ai' and not message['tool_calls']:
                summary.append(f"AI: {message['content']}")
            elif message['type'] == 'human':
                summary.append(f"Human: {message['content']}")
            elif message['type'] == 'tool':
                summary.append(f"Tool: {message['name']}\n  Response: {message['content'][:100]}")

        # Join summary into a single string
        summary_string = "\n\n".join(summary)

        # Print summary string
        return summary_string
    else:
        return "Did not find threads"

@action
def get_all_agents() -> str:
    """
    Gets all agent ids available.
    
    Returns:
        List of agent names and their ids.
    """

    resp = requests.get('http://127.0.0.1:8100/assistants/')
    agents = resp.json()

    agent_info = ""
    for agent in agents:
        agent_info += f"Name: {agent['name']}, ID: {agent['assistant_id']}\n"

    return f"Available agents are:\n{agent_info}"

@action
def get_agent_runbook(assistant_id: str) -> str:
    """
    Gets agent details.
    
    Args:
        assistant_id: Id of the assistant.

    Returns:
        Assistant details.
    """

    resp = requests.get(f'http://127.0.0.1:8100/assistants/{assistant_id}')

    try:
        response = resp.json()['config']['configurable']['type==agent/system_message']
    except:
        response = "Did not find the runbook"

    return response

@action
def update_agent_runbook(assistant_id: str, new_runbook: str) -> str:
    """
    Updates a runbook of an existing agent.
    
    Args:
        assistant_id: Id of the assistant.
        new_runbook: The new runbook to be changed to the assistant. Include a COMPLETE runbook, not just the updated parts.

    Returns:
        Assistant details.
    """

    resp = requests.get(f'http://127.0.0.1:8100/assistants/{assistant_id}')

    name = resp.json()['name']
    #prompt = f"You are an assistant with the following name: {name}.\nThe current date and time is: ${{CURRENT_DATETIME}}.\nYour instructions are:\n{new_runbook}"
    public = resp.json()['public']
    config = resp.json()['config']
    # print(config)
    config['configurable']['type==agent/system_message'] = new_runbook

    payload = {
        "name": name,
        "config": config,
        "public": public
    }

    response = requests.put(f'http://127.0.0.1:8100/assistants/{assistant_id}', json=payload)

    if response.status_code == 200:
        return f"Successfully updated!"
    else:
        return f"Failed with status code: {response.status_code}, message is {response.json()}"