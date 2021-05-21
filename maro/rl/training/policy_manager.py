# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC, abstractmethod
from collections import defaultdict, namedtuple
from os import getcwd
from typing import Dict, Union

from maro.rl.experience import ExperienceSet
from maro.rl.policy import AbsPolicy
from maro.utils import Logger

PolicyUpdateTrigger = namedtuple(
    "PolicyUpdateTrigger", ["min_new_experiences", "num_warmup_experiences"], defaults=[1]
)


class AbsPolicyManager(ABC):
    """Controller for policy updates.
    
    The actual policy instances may reside here or be distributed on a set of remote nodes.
    """
    def __init__(self):
        pass

    @property
    @abstractmethod
    def names(self):
        """Return the list of policy names."""
        raise NotImplementedError

    @abstractmethod
    def on_experiences(self, exp_by_policy: Dict[str, ExperienceSet]):
        """Logic for handling incoming experiences is implemented here."""
        raise NotImplementedError

    @abstractmethod
    def get_state(self):
        """Return the latest policy states."""
        raise NotImplementedError


class LocalPolicyManager(AbsPolicyManager):
    """Policy manager that contains the actual policy instances.

    Args:
        policy_dict (Dict[str, AbsPolicy]): A set of named policies.
        update_trigger (Union[PolicyUpdateTrigger, dict]): Conditions for triggering policy updates. If a
            dictionary is provided, the triggers will be applied to the policies by name. If a single
            ``PolicyUpdateTrigger`` is provided, the trigger will be applied to all updatable policies, i.e.,
            those that have the ``experience_memory`` attribute and the ``update`` interface. Defaults to
            None, in which case a default updatable trigger will be applied to every updatable policy, meaning that
            these policies will be updated as long as new experiences are available. 
        log_dir (str): Directory to store logs in. A ``Logger`` with tag "LEARNER" will be created at init time
            and this directory will be used to save the log files generated by it. Defaults to the current working
            directory.
    """
    def __init__(
        self,
        policy_dict: Dict[str, AbsPolicy],
        update_trigger: Union[PolicyUpdateTrigger, dict] = None,
        log_dir: str = getcwd()
    ):  
        if isinstance(update_trigger, dict) and not update_trigger.keys() <= policy_dict.keys():
            raise ValueError(f"The keys for update_trigger must be a subset of {list(policy_dict.keys())}")

        super().__init__()
        self._names = list(policy_dict.keys())
        self._logger = Logger("LOCAL_POLICY_MANAGER", dump_folder=log_dir)
        self.policy_dict = policy_dict

        self._updatable_policy_dict = {
            policy_id: policy for policy_id, policy in self.policy_dict.items()
            if hasattr(policy, "experience_memory") and hasattr(policy, "update")
        }
        if update_trigger is None:
            default_trigger = PolicyUpdateTrigger(min_new_experiences=1, num_warmup_experiences=1)
            self._update_trigger = {policy_id: default_trigger for policy_id in self._updatable_policy_dict}
        elif isinstance(update_trigger, dict):
            self._update_trigger = update_trigger
        else:
            self._update_trigger = {policy_id: update_trigger for policy_id in self._updatable_policy_dict}

        self._new_exp_counter = defaultdict(int)
        self._updated_policy_ids = set()

    @property
    def names(self):
        return self._names

    def on_experiences(self, exp_by_policy: Dict[str, ExperienceSet]):
        """Store experiences and update policies if possible.

        The incoming experiences are expected to be grouped by policy ID and will be stored in the corresponding
        policy's experience manager. Policies whose update conditions have been met will then be updated.
        """
        for policy_id, exp in exp_by_policy.items():
            policy = self.policy_dict[policy_id]
            if hasattr(policy, "experience_memory"):
                self._new_exp_counter[policy_id] += exp.size
                policy.experience_memory.put(exp)

        for policy_id, policy in self._updatable_policy_dict.items():
            print(f"Policy {policy_id}: exp mem size = {policy.experience_memory.size}, new exp = {self._new_exp_counter[policy_id]}")
            if (
                policy_id not in self._update_trigger or
                policy.experience_memory.size >= self._update_trigger[policy_id].num_warmup_experiences and
                self._new_exp_counter[policy_id] >= self._update_trigger[policy_id].min_new_experiences
            ):
                policy.update()
                self._new_exp_counter[policy_id] = 0
                self._updated_policy_ids.add(policy_id)

        if self._updated_policy_ids:
            self._logger.info(f"Updated policies {self._updated_policy_ids}")

    def get_state(self):
        """Return the states of updated policies since the last call."""
        policy_state_dict = {
            policy_id: self.policy_dict[policy_id].get_state() for policy_id in self._updated_policy_ids
        }
        self._updated_policy_ids.clear()
        return policy_state_dict
