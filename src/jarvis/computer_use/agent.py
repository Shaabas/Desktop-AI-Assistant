# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
from typing import Literal, Optional, Union, Any
from google import genai
from google.genai import types
import termcolor
from google.genai.types import (
    Part,
    GenerateContentConfig,
    Content,
    Candidate,
    FunctionResponse,
    FinishReason,
)
import time
import json
import re
from rich.console import Console
from rich.table import Table

from .computers import EnvState, Computer
from .safety_gate import preflight_safety_confirmations
from .no_progress_guard import RepeatedActionGuard

MAX_RECENT_TURN_WITH_SCREENSHOTS = 3
LEGACY_COMPUTER_USE_MODELS = [
    "gemini-2.5-computer-use-preview-10-2025",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
]

# Legacy predefined functions, which are used in gemini-2.5-computer-use-preview-10-2025, gemini-3-flash-preview and gemini-3.1-pro-preview.
LEGACY_PREDEFINED_COMPUTER_USE_FUNCTIONS = [
    "open_web_browser",
    "click_at",
    "hover_at",
    "type_text_at",
    "scroll_document",
    "scroll_at",
    "wait_5_seconds",
    "go_back",
    "go_forward",
    "search",
    "navigate",
    "key_combination",
    "drag_and_drop",
]

# Predefined functions which are used in gemini-3.5-flash and future models.
PREDEFINED_COMPUTER_USE_FUNCTIONS = [
    "click",
    "double_click",
    "triple_click",
    "middle_click",
    "right_click",
    "mouse_down",
    "mouse_up",
    "move",
    "type",
    "drag_and_drop",
    "wait",
    "press_key",
    "key_down",
    "key_up",
    "hotkey",
    "take_screenshot",
    "scroll",
    "go_back",
    "navigate",
    "go_forward",
]


console = Console()

# Built-in Computer Use tools will return "EnvState".
# Custom provided functions will return "dict".
FunctionResponseT = Union[EnvState, dict]


def multiply_numbers(x: float, y: float) -> dict:
    """Multiplies two numbers."""
    return {"result": x * y}


class BrowserAgent:
    def __init__(
        self,
        browser_computer: Computer,
        query: str,
        model_name: str,
        verbose: bool = True,
    ):
        self._browser_computer = browser_computer
        self._query = query
        self._model_name = model_name
        self._verbose = verbose
        self.final_reasoning = None
        self.security_aborted = False
        self.loop_guard_aborted = False
        self._no_progress_guard = RepeatedActionGuard(repeat_limit=3)
        use_vertex = os.environ.get("USE_VERTEXAI", "0").lower() in ["true", "1"]
        timeout_ms = int(os.environ.get("GEMINI_TIMEOUT_MS", 180000))
        self._client = genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY"),
            vertexai=use_vertex,
            project=os.environ.get("VERTEXAI_PROJECT") if use_vertex else None,
            location=os.environ.get("VERTEXAI_LOCATION") if use_vertex else None,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )
        initial_parts = [Part(text=self._query)]
        try:
            initial_state = browser_computer.current_state()
            if (
                initial_state
                and hasattr(initial_state, "screenshot")
                and isinstance(initial_state.screenshot, bytes)
                and len(initial_state.screenshot) > 0
            ):
                initial_parts.append(
                    Part.from_bytes(data=initial_state.screenshot, mime_type="image/png")
                )
        except Exception:
            pass

        self._contents: list[Content] = [
            Content(
                role="user",
                parts=initial_parts,
            )
        ]
        self._use_legacy_computer_use_function_call = (
            model_name in LEGACY_COMPUTER_USE_MODELS
        )

        # Exclude any predefined functions here.
        excluded_predefined_functions = []

        # Add your own custom functions here.
        custom_functions = [
            # For example:
            # types.FunctionDeclaration.from_callable(
            #     client=self._client, callable=multiply_numbers
            # )
        ]

        cu_env = getattr(browser_computer, "environment", None)
        if not isinstance(cu_env, types.Environment):
            cu_env = types.Environment.ENVIRONMENT_BROWSER

        tools = [
            types.Tool(
                computer_use=types.ComputerUse(
                    environment=cu_env,
                    excluded_predefined_functions=excluded_predefined_functions,
                ),
            ),
        ]
        if custom_functions:
            tools.append(types.Tool(function_declarations=custom_functions))

        system_instruction = None
        if cu_env == types.Environment.ENVIRONMENT_DESKTOP:
            system_instruction = (
                "You are an AI computer use agent controlling a Linux GNOME Shell desktop environment on Wayland.\n"
                "- Examine the desktop screenshot carefully before taking each action.\n"
                "- To launch an application: click its icon on the dock/dash, or press the Super key (Super_L) "
                "to open the Activities Overview, type the application name (e.g. 'text editor', 'terminal'), and press Return.\n"
                "- Do NOT assume Ubuntu shortcuts like Ctrl+Alt+T exist; use standard GNOME navigation.\n"
                "- Visually verify that a target window is open and focused before typing into it.\n"
                "- Use exact screen coordinates based on what you see in the screenshot."
            )

        # Thinking config breaks legacy computer use models like gemini-3-flash-preview
        thinking_cfg = (
            types.ThinkingConfig(include_thoughts=True)
            if not self._use_legacy_computer_use_function_call
            else None
        )

        self._generate_content_config = GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2,
            top_p=0.95,
            top_k=40,
            max_output_tokens=8192,
            tools=tools,
            thinking_config=thinking_cfg,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def handle_action(
        self, action: types.FunctionCall, use_legacy_actions: bool
    ) -> FunctionResponseT:
        """Handles the action and returns the environment state."""
        if use_legacy_actions:
            return self.handle_legacy_action(action)

        if action.name == "open_web_browser":
            return self._browser_computer.open_web_browser()
        elif action.name in ("click", "click_at"):
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.click_at(
                x=x,
                y=y,
            )
        elif action.name == "double_click":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.double_click_at(
                x=x,
                y=y,
            )
        elif action.name == "triple_click":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.triple_click_at(
                x=x,
                y=y,
            )
        elif action.name == "middle_click":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.middle_click_at(
                x=x,
                y=y,
            )
        elif action.name == "right_click":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.right_click_at(
                x=x,
                y=y,
            )
        elif action.name == "mouse_down":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.mouse_down(
                x=x,
                y=y,
            )
        elif action.name == "mouse_up":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.mouse_up(
                x=x,
                y=y,
            )
        elif action.name == "move":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.hover_at(
                x=x,
                y=y,
            )
        elif action.name == "type":
            press_enter = action.args.get("press_enter", False)
            return self._browser_computer.type_text(
                text=action.args["text"],
                press_enter=press_enter,
            )
        elif action.name == "scroll":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            magnitude = action.args.get("magnitude", 800)
            direction = action.args["direction"]

            if direction in ("up", "down"):
                magnitude = self.denormalize_y(magnitude)
            elif direction in ("left", "right"):
                magnitude = self.denormalize_x(magnitude)
            else:
                raise ValueError("Unknown direction: ", direction)
            return self._browser_computer.scroll_at(
                x=x, y=y, direction=direction, magnitude=magnitude
            )
        elif action.name == "wait":
            wait_seconds = int(action.args.get("seconds", 1))
            return self._browser_computer.wait(wait_seconds)
        elif action.name == "go_back":
            return self._browser_computer.go_back()
        elif action.name == "go_forward":
            return self._browser_computer.go_forward()
        elif action.name == "navigate":
            return self._browser_computer.navigate(action.args["url"])
        elif action.name in ("hotkey", "key_combination"):
            keys = action.args.get("keys") or [action.args.get("key")]
            return self._browser_computer.key_combination(keys)
        elif action.name == "press_key":
            return self._browser_computer.press_key(action.args["key"])
        elif action.name == "key_down":
            return self._browser_computer.key_down(action.args["key"])
        elif action.name == "key_up":
            return self._browser_computer.key_up(action.args["key"])
        elif action.name == "take_screenshot":
            return self._browser_computer.take_screenshot()
        elif action.name == "drag_and_drop":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            destination_x = self.denormalize_x(action.args["destination_x"])
            destination_y = self.denormalize_y(action.args["destination_y"])
            return self._browser_computer.drag_and_drop(
                x=x,
                y=y,
                destination_x=destination_x,
                destination_y=destination_y,
            )
        # Handle the custom function declarations here.
        elif action.name == multiply_numbers.__name__:
            return multiply_numbers(x=action.args["x"], y=action.args["y"])
        else:
            raise ValueError(f"Unsupported function: {action}")

    def handle_legacy_action(self, action: types.FunctionCall) -> FunctionResponseT:
        """Handles the action defined in the legacy models, and returns the environment state."""
        if action.name == "open_web_browser":
            return self._browser_computer.open_web_browser()
        elif action.name == "click_at":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.click_at(
                x=x,
                y=y,
            )
        elif action.name == "hover_at":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.hover_at(
                x=x,
                y=y,
            )
        elif action.name == "type_text_at":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            press_enter = action.args.get("press_enter", False)
            clear_before_typing = action.args.get("clear_before_typing", True)
            return self._browser_computer.type_text_at(
                x=x,
                y=y,
                text=action.args["text"],
                press_enter=press_enter,
                clear_before_typing=clear_before_typing,
            )
        elif action.name == "scroll_document":
            return self._browser_computer.scroll_document(action.args["direction"])
        elif action.name == "scroll_at":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            magnitude = action.args.get("magnitude", 800)
            direction = action.args["direction"]

            if direction in ("up", "down"):
                magnitude = self.denormalize_y(magnitude)
            elif direction in ("left", "right"):
                magnitude = self.denormalize_x(magnitude)
            else:
                raise ValueError("Unknown direction: ", direction)
            return self._browser_computer.scroll_at(
                x=x, y=y, direction=direction, magnitude=magnitude
            )
        elif action.name == "wait_5_seconds":
            return self._browser_computer.wait_5_seconds()

        elif action.name == "go_back":
            return self._browser_computer.go_back()
        elif action.name == "go_forward":
            return self._browser_computer.go_forward()
        elif action.name == "search":
            return self._browser_computer.search()
        elif action.name == "navigate":
            return self._browser_computer.navigate(action.args["url"])
        elif action.name == "key_combination":
            return self._browser_computer.key_combination(
                action.args["keys"].split("+")
            )
        elif action.name == "drag_and_drop":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            destination_x = self.denormalize_x(action.args["destination_x"])
            destination_y = self.denormalize_y(action.args["destination_y"])
            return self._browser_computer.drag_and_drop(
                x=x,
                y=y,
                destination_x=destination_x,
                destination_y=destination_y,
            )
        # Handle the custom function declarations here.
        elif action.name == multiply_numbers.__name__:
            return multiply_numbers(x=action.args["x"], y=action.args["y"])
        else:
            raise ValueError(f"Unsupported function: {action}")

    def get_model_response(
        self, max_retries=5, base_delay_s=1
    ) -> types.GenerateContentResponse:
        for attempt in range(max_retries):
            try:
                response = self._client.models.generate_content(
                    model=self._model_name,
                    contents=self._contents,
                    config=self._generate_content_config,
                )
                return response  # Return response on success
            except Exception as e:
                err_str = str(e)
                # Check for daily free-tier quota exhaustion (20 RPD cap)
                is_daily_quota = (
                    ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str)
                    and ("FreeTier" in err_str or "free_tier" in err_str or "limit: 20" in err_str)
                )
                if is_daily_quota:
                    print(e)
                    termcolor.cprint(
                        f"\n[QUOTA EXHAUSTED] Daily Free-Tier limit (20 requests/day) exceeded for model '{self._model_name}'.",
                        color="red",
                        attrs=["bold"],
                    )
                    termcolor.cprint(
                        "Solutions:\n"
                        "  1. Switch to an alternative model with available quota:\n"
                        "     --model gemini-3.5-flash-lite   (tested and ready)\n"
                        "     --model gemini-3-flash-preview  (tested and ready)\n"
                        "     (Or set GEMINI_MODEL=gemini-3.5-flash-lite in your .env file)\n"
                        "  2. Attach a Pay-As-You-Go billing account in Google AI Studio to unlock 1,000+ requests/min:\n"
                        "     https://aistudio.google.com/app/plan_information\n"
                        "  3. Switch to Vertex AI: set USE_VERTEXAI=true and VERTEXAI_PROJECT=<gcp-project> in .env\n",
                        color="yellow",
                    )
                    raise

                print(e)
                if attempt < max_retries - 1:
                    delay = base_delay_s * (2**attempt)
                    message = (
                        f"Generating content failed on attempt {attempt + 1}. "
                        f"Retrying in {delay} seconds...\n"
                    )
                    termcolor.cprint(
                        message,
                        color="yellow",
                    )
                    time.sleep(delay)
                else:
                    termcolor.cprint(
                        f"Generating content failed after {max_retries} attempts.\n",
                        color="red",
                    )
                    raise

    def get_text(self, candidate: Candidate) -> Optional[str]:
        """Extracts the user-facing text from the candidate, ignoring internal model thoughts."""
        if not candidate.content or not candidate.content.parts:
            return None
        text = []
        for part in candidate.content.parts:
            # Ignore internal thinking / thought tokens
            if getattr(part, "thought", False):
                continue
    @staticmethod
    def _strip_markdown_code_fences(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        return cleaned.strip()

    def get_text(self, candidate: Candidate) -> Optional[str]:
        """Extracts the user-facing text from the candidate, ignoring internal model thoughts."""
        if not candidate.content or not candidate.content.parts:
            return None
        text = []
        for part in candidate.content.parts:
            # Ignore internal thinking / thought tokens
            if getattr(part, "thought", False):
                continue
            if part.text:
                cleaned = self._strip_markdown_code_fences(part.text)
                # If the text is raw JSON action blocks, extract human text messages if present
                if (cleaned.startswith("[") or cleaned.startswith("{")) and (
                    '"type"' in cleaned or '"action"' in cleaned
                ):
                    try:
                        parsed = json.loads(cleaned)
                        if isinstance(parsed, list):
                            for item in parsed:
                                if item.get("type") == "text" and item.get("text"):
                                    text.append(item["text"])
                        elif isinstance(parsed, dict) and parsed.get("type") == "text":
                            text.append(parsed["text"])
                        continue
                    except Exception:
                        pass
                text.append(part.text)
        return " ".join(text) or None

    def _parse_json_action(self, item: dict) -> Optional[types.FunctionCall]:
        """Converts raw JSON action blocks emitted in text into types.FunctionCall."""
        if not isinstance(item, dict):
            return None
        action_type = item.get("action") or item.get("type")
        data = item.get("data", {}) if action_type == "computer" else item
        sub_type = data.get("action") or data.get("type", action_type)

        coord = data.get("point") or data.get("coordinate") or [data.get("x", 0), data.get("y", 0)]
        x, y = int(coord[0]), int(coord[1])

        if sub_type in ("key", "press_key"):
            key = data.get("name") or data.get("key") or "Super_L"
            if self._use_legacy_computer_use_function_call:
                return types.FunctionCall(name="key_combination", args={"keys": [key]})
            return types.FunctionCall(name="press_key", args={"key": key})
        elif sub_type in ("click", "click_at", "mouse_click"):
            if self._use_legacy_computer_use_function_call:
                return types.FunctionCall(name="click_at", args={"x": x, "y": y})
            return types.FunctionCall(name="click", args={"x": x, "y": y})
        elif sub_type in ("mouse_move", "move", "hover_at"):
            if self._use_legacy_computer_use_function_call:
                return types.FunctionCall(name="hover_at", args={"x": x, "y": y})
            return types.FunctionCall(name="move", args={"x": x, "y": y})
        elif sub_type in ("type", "type_text", "type_text_at"):
            text = data.get("text", "")
            if self._use_legacy_computer_use_function_call:
                return types.FunctionCall(
                    name="type_text_at", args={"x": 0, "y": 0, "text": text}
                )
            return types.FunctionCall(name="type", args={"text": text})
        elif sub_type in ("hotkey", "key_combination"):
            keys = data.get("keys", [])
            if self._use_legacy_computer_use_function_call:
                return types.FunctionCall(name="key_combination", args={"keys": keys})
            return types.FunctionCall(name="hotkey", args={"keys": keys})
        elif sub_type in ("scroll", "scroll_at", "scroll_document"):
            direction = data.get("direction", "down")
            if self._use_legacy_computer_use_function_call:
                return types.FunctionCall(
                    name="scroll_document", args={"direction": direction}
                )
            return types.FunctionCall(name="scroll", args={"direction": direction})
        elif sub_type in ("wait", "wait_5_seconds"):
            if self._use_legacy_computer_use_function_call:
                return types.FunctionCall(name="wait_5_seconds", args={})
            return types.FunctionCall(name="wait", args={})
        return None

    def extract_function_calls(self, candidate: Candidate) -> list[types.FunctionCall]:
        """Extracts the function call from the candidate (both protobuf and text JSON)."""
        if not candidate.content or not candidate.content.parts:
            return []
        ret = []
        for part in candidate.content.parts:
            if part.function_call:
                ret.append(part.function_call)
            elif part.text and not getattr(part, "thought", False):
                cleaned = self._strip_markdown_code_fences(part.text)
                if (cleaned.startswith("[") or cleaned.startswith("{")) and (
                    '"type"' in cleaned or '"action"' in cleaned
                ):
                    try:
                        raw_items = json.loads(cleaned)
                        if isinstance(raw_items, dict):
                            raw_items = [raw_items]
                        for item in raw_items:
                            fc = self._parse_json_action(item)
                            if fc:
                                ret.append(fc)
                    except Exception:
                        pass
        return ret

    def _preflight_safety_confirmations(self, function_calls: list[Any]) -> set[int] | None:
        """Resolve every model-declared confirmation before dispatching this batch."""
        def log_failure(exc: Exception) -> None:
            print(
                "[JARVIS] Confirmation failed closed; no actions from this "
                f"response will run ({type(exc).__name__}: {exc})."
            )

        return preflight_safety_confirmations(
            function_calls,
            self._get_safety_confirmation,
            on_error=log_failure,
        )

    def run_one_iteration(self) -> Literal["COMPLETE", "CONTINUE"]:
        # Check for any live dynamic user steering instructions from on-screen overlay
        if hasattr(self._browser_computer, "get_pending_instructions"):
            pending = self._browser_computer.get_pending_instructions()
            for instruction in pending:
                termcolor.cprint(
                    f"\n[Dynamic User Steering]: {instruction}",
                    color="cyan",
                    attrs=["bold"],
                )
                self._contents.append(
                    Content(
                        role="user",
                        parts=[
                            Part.from_text(
                                text=f"Live user instruction from overlay: {instruction}"
                            )
                        ],
                    )
                )

        if hasattr(self._browser_computer, "update_status"):
            self._browser_computer.update_status("Thinking...")

        # Generate a response from the model.
        if self._verbose:
            with console.status(
                "Generating response from Gemini Computer Use...", spinner_style=None
            ):
                try:
                    response = self.get_model_response()
                except Exception as e:
                    return "COMPLETE"
        else:
            try:
                response = self.get_model_response()
            except Exception as e:
                return "COMPLETE"

        if not response.candidates:
            if (
                response.prompt_feedback
                and response.prompt_feedback.block_reason == types.BlockReason.SAFETY
            ):
                raise ValueError(
                    f"Response was blocked due to safety. Feedback: {response.prompt_feedback}"
                )
            print("Response has no candidates!")
            print(response)
            raise ValueError("Empty response")

        # Extract the text and function call from the response.
        candidate = response.candidates[0]
        # Append the model turn to conversation history.
        if candidate.content:
            self._contents.append(candidate.content)

        reasoning = self.get_text(candidate)
        function_calls = self.extract_function_calls(candidate)

        # Retry the request in case of malformed FCs without valid actions.
        if not function_calls and candidate.finish_reason == FinishReason.MALFORMED_FUNCTION_CALL:
            termcolor.cprint(
                "Model produced a malformed function call. Retrying iteration...\n",
                color="yellow",
            )
            return "CONTINUE"

        if not function_calls:
            print(f"Agent Loop Complete: {reasoning or 'Finished.'}")
            self.final_reasoning = reasoning
            if hasattr(self._browser_computer, "update_status"):
                self._browser_computer.update_status("Task completed!")
            return "COMPLETE"

        # Update overlay status with current agent task / action
        if hasattr(self._browser_computer, "update_status"):
            fc = function_calls[0]
            name = fc.name
            args = fc.args or {}
            action_desc = ""
            if name == "mouse_click":
                action_desc = "clicking on target..."
            elif name == "left_click_at":
                action_desc = f"clicking at ({args.get('x', '')}, {args.get('y', '')})..."
            elif name in ("type_text_at", "type_text"):
                txt = args.get("text", "")
                action_desc = f"typing '{txt[:22]}...' " if len(txt) > 22 else f"typing '{txt}'..."
            elif name in ("keyboard_press", "press_key"):
                action_desc = f"pressing key: {args.get('key', '')}..."
            elif name == "key_combination":
                action_desc = f"pressing keys: {args.get('keys', '')}..."
            elif name == "navigate":
                action_desc = f"navigating to {args.get('url', '')}..."
            elif name == "wait":
                action_desc = "waiting for screen to settle..."
            elif name == "open_web_browser":
                action_desc = "opening web browser..."
            else:
                action_desc = f"performing {name}..."

            if reasoning and len(reasoning.strip()) > 0:
                first_line = reasoning.strip().split("\n")[0].strip("*# ").rstrip(".")
                if len(first_line) > 55:
                    first_line = first_line[:52] + "..."
                if not first_line.lower().startswith(("i am", "iam")):
                    status_text = f"I am {first_line[0].lower() + first_line[1:] if len(first_line) > 1 else first_line}"
                else:
                    status_text = first_line
            else:
                status_text = f"I am {action_desc}"

            self._browser_computer.update_status(status_text)

        function_call_strs = []
        for function_call in function_calls:
            # Print the function call and any reasoning.
            function_call_str = f"Name: {function_call.name}"
            if function_call.args:
                function_call_str += f"\nArgs:"
                for key, value in function_call.args.items():
                    function_call_str += f"\n  {key}: {value}"
            function_call_strs.append(function_call_str)

        table = Table(expand=True)
        table.add_column(
            "Gemini Computer Use Reasoning", header_style="magenta", ratio=1
        )
        table.add_column("Function Call(s)", header_style="cyan", ratio=1)
        table.add_row(reasoning, "\n".join(function_call_strs))
        if self._verbose:
            console.print(table)
            print()

        # Security invariant: no action from this model response is dispatched
        # until every safety decision attached to the response has been approved.
        # This prevents an earlier call in a multi-call batch (for example, a
        # submit click) from running before a later call opens the confirmation.
        approved_safety_indices = self._preflight_safety_confirmations(function_calls)
        if approved_safety_indices is None:
            self.security_aborted = True
            self.final_reasoning = (
                "Stopped because sensitive-action confirmation was denied or "
                "unavailable. No actions from the pending response were executed."
            )
            print("[JARVIS] Confirmation not approved; no actions from this response were executed.")
            return "COMPLETE"

        # This guard runs after confirmation preflight but before any function
        # dispatch. It prevents an endless loop of the same click/type/key action
        # while preserving the fail-closed confirmation invariant above.
        repeated_action_reason = self._no_progress_guard.observe(function_calls)
        if repeated_action_reason:
            self.loop_guard_aborted = True
            self.final_reasoning = repeated_action_reason
            print(f"[JARVIS] NO-PROGRESS GUARD: {repeated_action_reason}")
            if hasattr(self._browser_computer, "update_status"):
                self._browser_computer.update_status("Stopped: repeated action; inspect the screen")
            return "COMPLETE"

        function_responses = []
        for call_index, function_call in enumerate(function_calls):
            extra_fr_fields = {}
            if call_index in approved_safety_indices:
                # Explicitly mark only this call's safety check as acknowledged.
                extra_fr_fields["safety_acknowledgement"] = "true"
            try:
                if self._verbose:
                    with console.status(
                        "Sending command to Computer...", spinner_style=None
                    ):
                        fc_result = self.handle_action(
                            function_call, self._use_legacy_computer_use_function_call
                        )
                else:
                    fc_result = self.handle_action(
                        function_call, self._use_legacy_computer_use_function_call
                    )
            except PermissionError as exc:
                # A computer-proxy authorization failure is terminal for this
                # batch. Never continue with later calls after a blocked action.
                self.security_aborted = True
                self.final_reasoning = f"Sensitive action blocked: {exc}"
                print(
                    "[JARVIS] Sensitive action blocked; remaining actions in this "
                    "response will not run."
                )
                return "COMPLETE"
            if isinstance(fc_result, EnvState):
                function_responses.append(
                    FunctionResponse(
                        name=function_call.name,
                        response={
                            "url": fc_result.url,
                            **extra_fr_fields,
                        },
                        parts=[
                            types.FunctionResponsePart(
                                inline_data=types.FunctionResponseBlob(
                                    mime_type="image/png", data=fc_result.screenshot
                                )
                            )
                        ],
                    )
                )
            elif isinstance(fc_result, dict):
                response_payload = dict(fc_result)
                if extra_fr_fields:
                    response_payload.update(extra_fr_fields)
                function_responses.append(
                    FunctionResponse(name=function_call.name, response=response_payload)
                )
            else:
                # Do not silently omit a function response: malformed/incomplete
                # tool history can make the model repeat the previous action.
                function_responses.append(
                    FunctionResponse(
                        name=function_call.name,
                        response={
                            "error": (
                                "The action returned no usable environment state. "
                                "Do not assume it succeeded; inspect the current screen."
                            ),
                            **extra_fr_fields,
                        },
                    )
                )

        self._contents.append(
            Content(
                role="user",
                parts=[Part(function_response=fr) for fr in function_responses],
            )
        )

        # only keep screenshots in the few most recent turns, remove the screenshot images from the old turns.
        turn_with_screenshots_found = 0
        for content in reversed(self._contents):
            if content.role == "user" and content.parts:
                # check if content has screenshot of the predefined computer use functions.
                has_screenshot = False
                for part in content.parts:
                    if (
                        part.function_response
                        and part.function_response.parts
                        and part.function_response.name
                        in (PREDEFINED_COMPUTER_USE_FUNCTIONS + LEGACY_PREDEFINED_COMPUTER_USE_FUNCTIONS)
                    ):
                        has_screenshot = True
                        break

                if has_screenshot:
                    turn_with_screenshots_found += 1
                    # remove the screenshot image if the number of screenshots exceed the limit.
                    if turn_with_screenshots_found > MAX_RECENT_TURN_WITH_SCREENSHOTS:
                        for part in content.parts:
                            if (
                                part.function_response
                                and part.function_response.parts
                                and part.function_response.name
                                in (PREDEFINED_COMPUTER_USE_FUNCTIONS + LEGACY_PREDEFINED_COMPUTER_USE_FUNCTIONS)
                            ):
                                part.function_response.parts = None

        return "CONTINUE"

    def _get_safety_confirmation(
        self, safety: dict[str, Any]
    ) -> Literal["CONTINUE", "TERMINATE"]:
        if safety["decision"] != "require_confirmation":
            raise ValueError(f"Unknown safety decision: safety['decision']")
        termcolor.cprint(
            "Safety service requires explicit confirmation!",
            color="yellow",
            attrs=["bold"],
        )
        print(safety["explanation"])
        decision = ""
        while decision.lower() not in ("y", "n", "ye", "yes", "no"):
            decision = input("Do you wish to proceed? [Yes]/[No]\n")
        if decision.lower() in ("n", "no"):
            return "TERMINATE"
        return "CONTINUE"

    def agent_loop(self):
        status = "CONTINUE"
        while status == "CONTINUE":
            status = self.run_one_iteration()

    def denormalize_x(self, x: int) -> int:
        return int(x / 1000 * self._browser_computer.screen_size()[0])

    def denormalize_y(self, y: int) -> int:
        return int(y / 1000 * self._browser_computer.screen_size()[1])


# Alias for desktop or general computer use
ComputerAgent = BrowserAgent
