from __future__ import annotations

from abc import ABCMeta, abstractmethod
from collections import defaultdict
from itertools import count
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch

from maro.rl_v3.utils import ActionWithAux
from maro.rl_v3.utils import match_shape
from maro.rl_v3.utils.objects import SHAPE_CHECK_FLAG


class AbsPolicy(object):
    _policy_counter = defaultdict(count)

    def __init__(self, name: str, trainable: bool) -> None:
        super(AbsPolicy, self).__init__()

        self._name = name
        self._trainable = trainable

        print(f"Init {self.__class__.__name__}: {name}")

    @abstractmethod
    def get_actions(self, states: object) -> object:
        pass

    @property
    def name(self) -> str:
        return self._name

    @property
    def trainable(self) -> bool:
        return self._trainable


class DummyPolicy(AbsPolicy):
    def __init__(self) -> None:
        super(DummyPolicy, self).__init__(name='DUMMY_POLICY', trainable=False)

    def get_actions(self, states: object) -> object:
        return None


class RuleBasedPolicy(AbsPolicy, metaclass=ABCMeta):
    def __init__(self, name: str) -> None:
        super(RuleBasedPolicy, self).__init__(name=name, trainable=False)

    def get_actions(self, states: object) -> object:
        return self._rule(states)

    @abstractmethod
    def _rule(self, states: object) -> object:
        pass


class RLPolicy(AbsPolicy):
    def __init__(
        self,
        name: str,
        state_dim: int,
        action_dim: int,
        device: str = None,
        trainable: bool = True
    ) -> None:
        super(RLPolicy, self).__init__(name=name, trainable=trainable)
        self._state_dim = state_dim
        self._action_dim = action_dim
        self._device = torch.device(device) if device is not None \
            else torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._is_exploring = False

    @property
    def state_dim(self) -> int:
        return self._state_dim

    @property
    def action_dim(self) -> int:
        return self._action_dim

    @property
    def is_exploring(self) -> bool:
        return self._is_exploring

    def explore(self) -> None:
        self._is_exploring = True

    def exploit(self) -> None:
        self._is_exploring = False

    def ndarray_to_tensor(self, array: np.ndarray) -> torch.Tensor:
        return torch.from_numpy(array).to(self._device)

    @abstractmethod  # TODO
    def step(self, loss: torch.Tensor) -> None:
        pass

    @abstractmethod  # TODO
    def get_gradients(self, loss: torch.Tensor) -> Dict[str, torch.Tensor]:
        pass

    def get_actions_with_aux(self, states: np.ndarray) -> List[ActionWithAux]:
        actions, logps = self.get_actions_with_logps(states, require_logps=True)
        values = self.get_values_by_states_and_actions(states, actions)

        size = len(actions)
        actions_with_aux = []
        for i in range(size):
            actions_with_aux.append(ActionWithAux(
                action=actions[i],
                value=values[i] if values is not None else None,
                logp=logps[i] if logps is not None else None
            ))
        return actions_with_aux

    @abstractmethod
    def get_values_by_states_and_actions(self, states: np.ndarray, actions: np.ndarray) -> Optional[np.ndarray]:
        pass

    def get_actions(self, states: np.ndarray) -> np.ndarray:
        return self.get_actions_with_logps(states, require_logps=False)[0]

    def get_actions_tensor(self, states: torch.Tensor) -> torch.Tensor:
        return self.get_actions_with_logps_tensor(states, require_logps=False)[0]

    @abstractmethod
    def _get_actions_with_logps_impl(
        self, states: torch.Tensor, exploring: bool, require_logps: bool
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        pass

    def get_actions_with_logps(
        self, states: np.ndarray, require_logps: bool = True
    ) -> Tuple[np.ndarray, Optional[np.ndarray]]:
        actions, logps = self.get_actions_with_logps_tensor(self.ndarray_to_tensor(states), require_logps)
        return actions.cpu().numpy(), logps.cpu().numpy() if logps is not None else None

    def get_actions_with_logps_tensor(
        self, states: torch.Tensor, require_logps: bool = True
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        assert self._shape_check(states=states)
        actions, logps = self._get_actions_with_logps_impl(states, self._is_exploring, require_logps)
        assert self._shape_check(states=states, actions=actions)  # [B, action_dim]
        assert logps is None or match_shape(logps, (states.shape[0],))  # [B]
        if SHAPE_CHECK_FLAG:
            assert self._post_check(states=states, actions=actions)
        return actions, logps

    @abstractmethod
    def freeze(self) -> None:
        pass

    @abstractmethod
    def unfreeze(self) -> None:
        pass

    @abstractmethod
    def eval(self) -> None:
        pass

    @abstractmethod
    def train(self) -> None:
        pass

    @abstractmethod
    def get_policy_state(self) -> object:
        pass

    @abstractmethod
    def set_policy_state(self, policy_state: object) -> None:
        pass

    @abstractmethod
    def soft_update(self, other_policy: RLPolicy, tau: float) -> None:
        pass

    def _shape_check(
        self,
        states: torch.Tensor,
        actions: Optional[torch.Tensor] = None
    ) -> bool:
        if not SHAPE_CHECK_FLAG:
            return True
        else:
            if states.shape[0] == 0:
                return False
            if not match_shape(states, (None, self.state_dim)):
                return False

            if actions is not None:
                if not match_shape(actions, (states.shape[0], self.action_dim)):
                    return False
            return True

    @abstractmethod
    def _post_check(self, states: torch.Tensor, actions: torch.Tensor) -> bool:
        pass


if __name__ == '__main__':
    data = [AbsPolicy('Jack', True), AbsPolicy('Tom', True), DummyPolicy(), DummyPolicy()]
    for policy in data:
        print(policy.name)
