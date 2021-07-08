# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import time
from os import getcwd
from typing import List, Union

from maro.communication import Proxy, SessionMessage, SessionType
from maro.rl.utils import MsgKey, MsgTag
from maro.utils import Logger

from ..agent_wrapper import AgentWrapper
from ..env_wrapper import AbsEnvWrapper


def actor(
    group: str,
    actor_idx: int,
    env_wrapper: AbsEnvWrapper,
    agent_wrapper: AgentWrapper,
    num_episodes: int,
    num_steps: int = -1,
    eval_env_wrapper: AbsEnvWrapper = None,
    eval_schedule: Union[int, List[int]] = None,
    log_env_summary: bool = True,
    proxy_kwargs: dict = {},
    log_dir: str = getcwd()
):
    """Controller for single-threaded learning workflows.

    Args:
        group (str): Group name for the cluster that includes the server and all actors.
        actor_idx (int): Integer actor index. The actor's ID in the cluster will be "ACTOR.{actor_idx}".
        env_wrapper (AbsEnvWrapper): Environment wrapper for training data collection.
        agent_wrapper (AgentWrapper): Agent wrapper to interact with the environment wrapper.
        num_episodes (int): Number of training episodes. Each training episode may contain one or more
            collect-update cycles, depending on how the implementation of the roll-out manager.
        num_steps (int): Number of environment steps to roll out in each call to ``collect``. Defaults to -1, in
            which case the roll-out will be executed until the end of the environment.
        eval_env_wrapper_func (AbsEnvWrapper): Environment wrapper for evaluation. If this is None, the training
            environment wrapper will be used for evaluation. Defaults to None.
        eval_schedule (Union[int, List[int]]): Evaluation schedule. If an integer is provided, the policies will
            will be evaluated every ``eval_schedule`` episodes. If a list is provided, the policies will be evaluated
            at the end of the training episodes given in the list. In any case, the policies will be evaluated
            at the end of the last training episode. Defaults to None, in which case the policies will only be
            evaluated after the last training episode.
        log_env_summary (bool): If True, the ``summary`` property of the environment wrapper will be logged at the end
            of each episode. Defaults to True.
        proxy_kwargs: Keyword parameters for the internal ``Proxy`` instance. See ``Proxy`` class
            for details. Defaults to the empty dictionary.
        log_dir (str): Directory to store logs in. A ``Logger`` with tag "LOCAL_ROLLOUT_MANAGER" will be created at init
            time and this directory will be used to save the log files generated by it. Defaults to the current working
            directory.
    """
    if num_steps == 0 or num_steps < -1:
        raise ValueError("num_steps must be a positive integer or -1")

    eval_env_wrapper = env_wrapper if not eval_env_wrapper else eval_env_wrapper
    peers = {"policy_server": 1}
    proxy = Proxy(group, "actor", peers, component_name=f"ACTOR.{actor_idx}", **proxy_kwargs)
    policy_server_address = proxy.peers["policy_server"][0]
    logger = Logger(proxy.name, dump_folder=log_dir)
    policy_version = None

    # evaluation schedule
    if eval_schedule is None:
        eval_schedule = []
    elif isinstance(eval_schedule, int):
        num_eval_schedule = num_episodes // eval_schedule
        eval_schedule = [eval_schedule * i for i in range(1, num_eval_schedule + 1)]
    else:
        eval_schedule.sort()

    # always evaluate after the last episode
    if not eval_schedule or num_episodes != eval_schedule[-1]:
        eval_schedule.append(num_episodes)

    eval_point_index = 0

    # get initial policy states from the policy manager
    msg = SessionMessage(MsgTag.GET_INITIAL_POLICY_STATE, proxy.name, policy_server_address)
    reply = proxy.send(msg)[0]
    policy_version = reply.body[MsgKey.VERSION]
    agent_wrapper.set_policy_states(reply.body[MsgKey.POLICY_STATE])

    # main loop
    for ep in range(1, num_episodes + 1):
        t0 = time.time()
        num_experiences_collected = 0
        agent_wrapper.explore()
        env_wrapper.reset()
        env_wrapper.start()  # get initial state
        segment = 0
        while env_wrapper.state:
            segment += 1
            logger.info(
                f"Collecting simulation data (episode {ep}, segment {segment}, policy version {policy_version})"
            )
            start_step_index = env_wrapper.step_index + 1
            steps_to_go = num_steps
            while env_wrapper.state and steps_to_go:
                env_wrapper.step(agent_wrapper.choose_action(env_wrapper.state))
                steps_to_go -= 1

            logger.info(
                f"Roll-out finished (episode {ep}, segment {segment}, "
                f"steps {start_step_index} - {env_wrapper.step_index})"
            )

            exp_by_agent = env_wrapper.get_experiences()
            policies_with_new_exp = agent_wrapper.store_experiences(exp_by_agent)
            num_experiences_collected += sum(exp.size for exp in exp_by_agent.values())
            exp_by_policy = agent_wrapper.get_experiences_by_policy(policies_with_new_exp)
            reply = proxy.send(
                SessionMessage(
                    MsgTag.COLLECT_DONE, proxy.name, policy_server_address,
                    body={MsgKey.EXPERIENCES: exp_by_policy, MsgKey.VERSION: policy_version}
                )
            )[0]
            policy_version = reply.body[MsgKey.VERSION]
            agent_wrapper.set_policy_states(reply.body[MsgKey.POLICY_STATE])

        # update the exploration parameters
        agent_wrapper.exploration_step()

        # performance details
        if log_env_summary:
            logger.info(f"ep {ep}: {env_wrapper.summary}")

        logger.info(
            f"ep {ep} summary - "
            f"running time: {time.time() - t0} "
            f"env steps: {env_wrapper.step_index} "
            f"experiences collected: {num_experiences_collected}"
        )
        if ep == eval_schedule[eval_point_index]:
            # evaluation
            eval_point_index += 1
            logger.info("Evaluating...")
            agent_wrapper.exploit()
            eval_env_wrapper.reset()
            eval_env_wrapper.start()  # get initial state
            while eval_env_wrapper.state:
                action = agent_wrapper.choose_action(eval_env_wrapper.state)
                eval_env_wrapper.step(action)

            # performance details
            logger.info(f"Evaluation result: {eval_env_wrapper.summary}")

    # tell the policy server I'm all done.
    proxy.isend(SessionMessage(MsgTag.DONE, proxy.name, policy_server_address, session_type=SessionType.NOTIFICATION))
    proxy.close()
