import argparse
import os
import pathlib
import platform
import sys
from ast import literal_eval
from pathlib import Path
from types import UnionType
from typing import Any, MutableMapping, get_args, get_origin
from uuid import uuid4

import toml
from dotenv import load_dotenv
from pydantic import BaseModel, SecretStr, ValidationError

import openhands
from openhands.core import logger
from openhands.core.config.agent_config import AgentConfig
from openhands.core.config.app_config import AppConfig
from openhands.core.config.config_utils import (
    OH_DEFAULT_AGENT,
    OH_MAX_ITERATIONS,
)
from openhands.core.config.llm_config import LLMConfig
from openhands.core.config.sandbox_config import SandboxConfig
from openhands.core.config.security_config import SecurityConfig
from openhands.storage import get_file_store
from openhands.storage.files import FileStore

JWT_SECRET = '.jwt_secret'
load_dotenv()


def load_from_env(
    cfg: AppConfig, env_or_toml_dict: dict | MutableMapping[str, str]
) -> None:
    """Sets config attributes from environment variables or TOML dictionary.

    Reads environment-style variables and updates the config attributes accordingly.
    Supports configuration of LLM settings (e.g., LLM_BASE_URL), agent settings
    (e.g., AGENT_MEMORY_ENABLED), sandbox settings (e.g., SANDBOX_TIMEOUT), and more.

    Args:
        cfg: The AppConfig object to set attributes on.
        env_or_toml_dict: The environment variables or a config.toml dict.
    """

    def get_optional_type(union_type: UnionType) -> Any:
        """Returns the non-None type from a Union."""
        types = get_args(union_type)
        return next((t for t in types if t is not type(None)), None)

    # helper function to set attributes based on env vars
    def set_attr_from_env(sub_config: BaseModel, prefix='') -> None:
        """Set attributes of a config model based on environment variables."""
        for field_name, field_info in sub_config.model_fields.items():
            field_value = getattr(sub_config, field_name)
            field_type = field_info.annotation

            # compute the expected env var name from the prefix and field name
            # e.g. LLM_BASE_URL
            env_var_name = (prefix + field_name).upper()

            if isinstance(field_value, BaseModel):
                set_attr_from_env(field_value, prefix=field_name + '_')

            elif env_var_name in env_or_toml_dict:
                # convert the env var to the correct type and set it
                value = env_or_toml_dict[env_var_name]

                # skip empty config values (fall back to default)
                if not value:
                    continue

                try:
                    # if it's an optional type, get the non-None type
                    if get_origin(field_type) is UnionType:
                        field_type = get_optional_type(field_type)

                    # Attempt to cast the env var to type hinted in the dataclass
                    if field_type is bool:
                        cast_value = str(value).lower() in ['true', '1']
                    # parse dicts like SANDBOX_RUNTIME_STARTUP_ENV_VARS
                    elif get_origin(field_type) is dict:
                        cast_value = literal_eval(value)
                    else:
                        cast_value = field_type(value)
                    setattr(sub_config, field_name, cast_value)
                except (ValueError, TypeError):
                    logger.openhands_logger.error(
                        f'Error setting env var {env_var_name}={value}: check that the value is of the right type'
                    )

    # Start processing from the root of the config object
    set_attr_from_env(cfg)

    # load default LLM config from env
    default_llm_config = cfg.get_llm_config()
    set_attr_from_env(default_llm_config, 'LLM_')
    # load default agent config from env
    default_agent_config = cfg.get_agent_config()
    set_attr_from_env(default_agent_config, 'AGENT_')


def load_from_toml(cfg: AppConfig, toml_file: str = 'config.toml') -> None:
    """Load the config from the toml file. Supports both styles of config vars.

    Args:
        cfg: The AppConfig object to update attributes of.
        toml_file: The path to the toml file. Defaults to 'config.toml'.

    See Also:
    - config.template.toml for the full list of config options.
    """
    # try to read the config.toml file into the config object
    try:
        with open(toml_file, 'r', encoding='utf-8') as toml_contents:
            toml_config = toml.load(toml_contents)
    except FileNotFoundError:
        return
    except toml.TomlDecodeError as e:
        logger.openhands_logger.warning(
            f'Cannot parse config from toml, toml values have not been applied.\nError: {e}',
        )
        return

    # if there was an exception or core is not in the toml, try to use the old-style toml
    if 'core' not in toml_config:
        # re-use the env loader to set the config from env-style vars
        load_from_env(cfg, toml_config)
        return

    core_config = toml_config['core']

    # load llm configs and agent configs
    for key, value in toml_config.items():
        if isinstance(value, dict):
            try:
                if key is not None and key.lower() == 'agent':
                    # Every entry here is either a field for the default `agent` config group, or itself a group
                    # The best way to tell the difference is to try to parse it as an AgentConfig object
                    agent_group_ids: set[str] = set()
                    for nested_key, nested_value in value.items():
                        if isinstance(nested_value, dict):
                            try:
                                agent_config = AgentConfig(**nested_value)
                            except ValidationError:
                                continue
                            agent_group_ids.add(nested_key)
                            cfg.set_agent_config(agent_config, nested_key)

                    logger.openhands_logger.debug(
                        'Attempt to load default agent config from config toml'
                    )
                    value_without_groups = {
                        k: v for k, v in value.items() if k not in agent_group_ids
                    }
                    agent_config = AgentConfig(**value_without_groups)
                    cfg.set_agent_config(agent_config, 'agent')

                elif key is not None and key.lower() == 'llm':
                    # Every entry here is either a field for the default `llm` config group, or itself a group
                    # The best way to tell the difference is to try to parse it as an LLMConfig object
                    llm_group_ids: set[str] = set()
                    for nested_key, nested_value in value.items():
                        if isinstance(nested_value, dict):
                            try:
                                llm_config = LLMConfig(**nested_value)
                            except ValidationError:
                                continue
                            llm_group_ids.add(nested_key)
                            cfg.set_llm_config(llm_config, nested_key)

                    logger.openhands_logger.debug(
                        'Attempt to load default LLM config from config toml'
                    )

                    # Extract generic LLM fields, which are not nested LLM configs
                    generic_llm_fields = {}
                    for k, v in value.items():
                        if not isinstance(v, dict):
                            generic_llm_fields[k] = v
                    generic_llm_config = LLMConfig(**generic_llm_fields)
                    cfg.set_llm_config(generic_llm_config, 'llm')

                    # Process custom named LLM configs
                    for nested_key, nested_value in value.items():
                        if isinstance(nested_value, dict):
                            logger.openhands_logger.debug(
                                f'Processing custom LLM config "{nested_key}":'
                            )
                            # Apply generic LLM config with custom LLM overrides, e.g.
                            # [llm]
                            # model="..."
                            # num_retries = 5
                            # [llm.claude]
                            # model="claude-3-5-sonnet"
                            # results in num_retries APPLIED to claude-3-5-sonnet
                            custom_fields = {}
                            for k, v in nested_value.items():
                                if not isinstance(v, dict):
                                    custom_fields[k] = v
                            merged_llm_dict = generic_llm_fields.copy()
                            merged_llm_dict.update(custom_fields)

                            custom_llm_config = LLMConfig(**merged_llm_dict)
                            cfg.set_llm_config(custom_llm_config, nested_key)

                elif key is not None and key.lower() == 'security':
                    logger.openhands_logger.debug(
                        'Attempt to load security config from config toml'
                    )
                    security_config = SecurityConfig(**value)
                    cfg.security = security_config
                elif not key.startswith('sandbox') and key.lower() != 'core':
                    logger.openhands_logger.warning(
                        f'Unknown key in {toml_file}: "{key}"'
                    )
            except (TypeError, KeyError, ValidationError) as e:
                logger.openhands_logger.warning(
                    f'Cannot parse [{key}] config from toml, values have not been applied.\nError: {e}',
                )
        else:
            logger.openhands_logger.warning(f'Unknown section [{key}] in {toml_file}')

    try:
        # set sandbox config from the toml file
        sandbox_config = cfg.sandbox

        # migrate old sandbox configs from [core] section to sandbox config
        keys_to_migrate = [key for key in core_config if key.startswith('sandbox_')]
        for key in keys_to_migrate:
            new_key = key.replace('sandbox_', '')
            if new_key in sandbox_config.__annotations__:
                # read the key in sandbox and remove it from core
                setattr(sandbox_config, new_key, core_config.pop(key))
            else:
                logger.openhands_logger.warning(
                    f'Unknown config key "{key}" in [sandbox] section'
                )

        # the new style values override the old style values
        if 'sandbox' in toml_config:
            sandbox_config = SandboxConfig(**toml_config['sandbox'])

        # update the config object with the new values
        cfg.sandbox = sandbox_config
        for key, value in core_config.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
            else:
                logger.openhands_logger.warning(
                    f'Unknown config key "{key}" in [core] section'
                )
    except (TypeError, KeyError, ValidationError) as e:
        logger.openhands_logger.warning(
            f'Cannot parse [sandbox] config from toml, values have not been applied.\nError: {e}',
        )


def get_or_create_jwt_secret(file_store: FileStore) -> str:
    try:
        jwt_secret = file_store.read(JWT_SECRET)
        return jwt_secret
    except FileNotFoundError:
        new_secret = uuid4().hex
        file_store.write(JWT_SECRET, new_secret)
        return new_secret


def finalize_config(cfg: AppConfig):
    """More tweaks to the config after it's been loaded."""
    if cfg.workspace_base is not None:
        cfg.workspace_base = os.path.abspath(cfg.workspace_base)
        if cfg.workspace_mount_path is None:
            cfg.workspace_mount_path = cfg.workspace_base

        if cfg.workspace_mount_rewrite:
            base = cfg.workspace_base or os.getcwd()
            parts = cfg.workspace_mount_rewrite.split(':')
            cfg.workspace_mount_path = base.replace(parts[0], parts[1])

    # make sure log_completions_folder is an absolute path
    for llm in cfg.llms.values():
        llm.log_completions_folder = os.path.abspath(llm.log_completions_folder)
        if llm.embedding_base_url is None:
            llm.embedding_base_url = llm.base_url

    if cfg.sandbox.use_host_network and platform.system() == 'Darwin':
        logger.openhands_logger.warning(
            'Please upgrade to Docker Desktop 4.29.0 or later to use host network mode on macOS. '
            'See https://github.com/docker/roadmap/issues/238#issuecomment-2044688144 for more information.'
        )

    # make sure cache dir exists
    if cfg.cache_dir:
        pathlib.Path(cfg.cache_dir).mkdir(parents=True, exist_ok=True)

    if not cfg.jwt_secret:
        cfg.jwt_secret = SecretStr(
            get_or_create_jwt_secret(
                get_file_store(cfg.file_store, cfg.file_store_path)
            )
        )


def get_agent_config_arg(
    agent_config_arg: str, toml_file: str = 'config.toml'
) -> AgentConfig | None:
    """Get a group of agent settings from the config file.

    A group in config.toml can look like this:

    ```
    [agent.default]
    enable_prompt_extensions = false
    ```

    The user-defined group name, like "default", is the argument to this function. The function will load the AgentConfig object
    with the settings of this group, from the config file, and set it as the AgentConfig object for the app.

    Note that the group must be under "agent" group, or in other words, the group name must start with "agent.".

    Args:
        agent_config_arg: The group of agent settings to get from the config.toml file.
        toml_file: Path to the configuration file to read from. Defaults to 'config.toml'.

    Returns:
        AgentConfig: The AgentConfig object with the settings from the config file.
    """
    # keep only the name, just in case
    agent_config_arg = agent_config_arg.strip('[]')

    # truncate the prefix, just in case
    if agent_config_arg.startswith('agent.'):
        agent_config_arg = agent_config_arg[6:]

    logger.openhands_logger.debug(f'Loading agent config from {agent_config_arg}')

    # load the toml file
    try:
        with open(toml_file, 'r', encoding='utf-8') as toml_contents:
            toml_config = toml.load(toml_contents)
    except FileNotFoundError as e:
        logger.openhands_logger.error(f'Config file not found: {e}')
        return None
    except toml.TomlDecodeError as e:
        logger.openhands_logger.error(
            f'Cannot parse agent group from {agent_config_arg}. Exception: {e}'
        )
        return None

    # update the agent config with the specified section
    if 'agent' in toml_config and agent_config_arg in toml_config['agent']:
        return AgentConfig(**toml_config['agent'][agent_config_arg])
    logger.openhands_logger.debug(f'Loading from toml failed for {agent_config_arg}')
    return None


def get_llm_config_arg(
    llm_config_arg: str, toml_file: str = 'config.toml'
) -> LLMConfig | None:
    """Get a group of llm settings from the config file.

    A group in config.toml can look like this:

    ```
    [llm.gpt-3.5-for-eval]
    model = 'gpt-3.5-turbo'
    api_key = '...'
    temperature = 0.5
    num_retries = 8
    ...
    ```

    The user-defined group name, like "gpt-3.5-for-eval", is the argument to this function. The function will load the LLMConfig object
    with the settings of this group, from the config file, and set it as the LLMConfig object for the app.

    Note that the group must be under "llm" group, or in other words, the group name must start with "llm.".

    Args:
        llm_config_arg: The group of llm settings to get from the config.toml file.
        toml_file: Path to the configuration file to read from. Defaults to 'config.toml'.

    Returns:
        LLMConfig: The LLMConfig object with the settings from the config file.
    """
    # keep only the name, just in case
    llm_config_arg = llm_config_arg.strip('[]')

    # truncate the prefix, just in case
    if llm_config_arg.startswith('llm.'):
        llm_config_arg = llm_config_arg[4:]

    logger.openhands_logger.debug(f'Loading llm config from {llm_config_arg}')

    # load the toml file
    try:
        default_dir = Path(openhands.__file__).parent.parent
        print('default dir: ', default_dir)
        with open(default_dir / toml_file, 'r', encoding='utf-8') as toml_contents:
            toml_config = toml.load(toml_contents)
    except FileNotFoundError as e:
        logger.openhands_logger.error(f'Config file not found: {e}')
        return None
    except toml.TomlDecodeError as e:
        logger.openhands_logger.error(
            f'Cannot parse llm group from {llm_config_arg}. Exception: {e}'
        )
        return None

    # update the llm config with the specified section
    if 'llm' in toml_config and llm_config_arg in toml_config['llm']:
        return LLMConfig(**toml_config['llm'][llm_config_arg])
    logger.openhands_logger.debug(f'Loading from toml failed for {llm_config_arg}')
    return None


# Command line arguments
def get_parser() -> argparse.ArgumentParser:
    """Get the argument parser."""
    parser = argparse.ArgumentParser(description='Run the agent via CLI')

    # Add version argument
    parser.add_argument(
        '-v', '--version', action='store_true', help='Show version information'
    )

    parser.add_argument(
        '--config-file',
        type=str,
        default='config.toml',
        help='Path to the config file (default: config.toml in the current directory)',
    )
    parser.add_argument(
        '-d',
        '--directory',
        type=str,
        help='The working directory for the agent',
    )
    parser.add_argument(
        '-t',
        '--task',
        type=str,
        default='',
        help='The task for the agent to perform',
    )
    parser.add_argument(
        '-f',
        '--file',
        type=str,
        help='Path to a file containing the task. Overrides -t if both are provided.',
    )
    parser.add_argument(
        '-c',
        '--agent-cls',
        default=OH_DEFAULT_AGENT,
        type=str,
        help='Name of the default agent to use',
    )
    parser.add_argument(
        '-i',
        '--max-iterations',
        default=OH_MAX_ITERATIONS,
        type=int,
        help='The maximum number of iterations to run the agent',
    )
    parser.add_argument(
        '-b',
        '--max-budget-per-task',
        type=float,
        help='The maximum budget allowed per task, beyond which the agent will stop.',
    )
    # --eval configs are for evaluations only
    parser.add_argument(
        '--eval-output-dir',
        default='evaluation/evaluation_outputs/outputs',
        type=str,
        help='The directory to save evaluation output',
    )
    parser.add_argument(
        '--eval-n-limit',
        default=None,
        type=int,
        help='The number of instances to evaluate',
    )
    parser.add_argument(
        '--eval-num-workers',
        default=4,
        type=int,
        help='The number of workers to use for evaluation',
    )
    parser.add_argument(
        '--eval-note',
        default=None,
        type=str,
        help='The note to add to the evaluation directory',
    )
    parser.add_argument(
        '-l',
        '--llm-config',
        default=None,
        type=str,
        help='Replace default LLM ([llm] section in config.toml) config with the specified LLM config, e.g. "llama3" for [llm.llama3] section in config.toml',
    )
    parser.add_argument(
        '--agent-config',
        default=None,
        type=str,
        help='Replace default Agent ([agent] section in config.toml) config with the specified Agent config, e.g. "CodeAct" for [agent.CodeAct] section in config.toml',
    )
    parser.add_argument(
        '-n',
        '--name',
        default='',
        type=str,
        help='Name for the session',
    )
    parser.add_argument(
        '--eval-ids',
        default=None,
        type=str,
        help='The comma-separated list (in quotes) of IDs of the instances to evaluate',
    )
    parser.add_argument(
        '--no-auto-continue',
        action='store_true',
        help='Disable automatic "continue" responses in headless mode. Will read from stdin instead.',
    )
    return parser


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = get_parser()
    args = parser.parse_args()

    if args.version:
        from openhands import __version__

        print(f'OpenHands version: {__version__}')
        sys.exit(0)

    return args


def load_app_config(
    set_logging_levels: bool = True, config_file: str = 'config.toml'
) -> AppConfig:
    """Load the configuration from the specified config file and environment variables.

    Args:
        set_logging_levels: Whether to set the global variables for logging levels.
        config_file: Path to the config file. Defaults to 'config.toml' in the current directory.
    """
    config = AppConfig()
    load_from_toml(config, config_file)
    load_from_env(config, os.environ)
    finalize_config(config)
    if set_logging_levels:
        logger.DEBUG = config.debug
        logger.DISABLE_COLOR_PRINTING = config.disable_color
    return config


def setup_config_from_args(args: argparse.Namespace) -> AppConfig:
    """Load config from toml and override with command line arguments.

    Common setup used by both CLI and main.py entry points.
    """
    # Load base config from toml and env vars
    config = load_app_config(config_file=args.config_file)

    # Override with command line arguments if provided
    if args.llm_config:
        # if we didn't already load it, get it from the toml file
        if args.llm_config not in config.llms:
            llm_config = get_llm_config_arg(args.llm_config)
        else:
            llm_config = config.llms[args.llm_config]
        if llm_config is None:
            raise ValueError(f'Invalid toml file, cannot read {args.llm_config}')
        config.set_llm_config(llm_config)

    # Override default agent if provided
    if args.agent_cls:
        config.default_agent = args.agent_cls

    # Set max iterations and max budget per task if provided, otherwise fall back to config values
    if args.max_iterations is not None:
        config.max_iterations = args.max_iterations
    if args.max_budget_per_task is not None:
        config.max_budget_per_task = args.max_budget_per_task

    return config
