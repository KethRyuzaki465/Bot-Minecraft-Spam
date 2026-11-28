import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bot import Bot, MODE_COMBAT, MODE_LOOT


class SwordFightTests(unittest.TestCase):
    def setUp(self):
        self.player = Bot(0, 'localhost', 25565)
        self.client = self.player.bot = Mock()
        self.combat = self.player.combat
        self.client.health = 20
        self.client.heldItem = SimpleNamespace(name='iron_sword')
        self.client.world.raycast.return_value = None
        self.player.set_mode(MODE_COMBAT)
        self.target = SimpleNamespace(id=1, height=1.8, position=Mock())
        self.aim = self.target.position.offset.return_value
        self.delta = self.aim.minus.return_value
        self.delta.norm.return_value = 2.5
        self.client.entity.eyeHeight = 1.62
        self.delta.scaled.return_value = Mock()

    def test_chases_without_swinging_at_four_blocks(self):
        self.delta.norm.return_value = 4.0
        self.combat.tick(self.client, self.target, 4.0, 10.0)
        self.client.attack.assert_not_called()
        self.client.setControlState.assert_any_call('forward', True)
        self.client.setControlState.assert_any_call('sprint', True)

    def test_wall_blocks_swing(self):
        self.client.world.raycast.return_value = object()
        self.combat.tick(self.client, self.target, 2.5, 10.0)
        self.client.attack.assert_not_called()

    def test_vertical_separation_can_put_aim_point_out_of_reach(self):
        self.delta.norm.return_value = 3.2
        self.combat.tick(self.client, self.target, 2.9, 10.0)
        self.client.attack.assert_not_called()

    def test_sword_recovery_and_post_hit_release(self):
        self.combat.tick(self.client, self.target, 2.5, 10.0)
        self.client.attack.assert_called_once_with(self.target)

        self.client.setControlState.reset_mock()
        self.combat.tick(self.client, self.target, 2.6, 10.05)
        self.client.setControlState.assert_any_call('forward', False)
        self.client.setControlState.assert_any_call('sprint', False)

        self.combat.tick(self.client, self.target, 2.5, 10.5)
        self.client.attack.assert_called_once_with(self.target)
        self.combat.tick(self.client, self.target, 2.5, 10.7)
        self.assertEqual(self.client.attack.call_count, 2)

    def test_backs_up_when_crowded(self):
        self.combat.tick(self.client, self.target, 1.5, 10.0)
        self.client.setControlState.assert_any_call('back', True)
        self.client.setControlState.assert_any_call('forward', False)

    def test_does_not_swing_while_inventory_uses_hand(self):
        with self.combat.hand_lock:
            self.combat.tick(self.client, self.target, 2.5, 10.0)
        self.client.attack.assert_not_called()

    def test_equips_best_available_sword_over_other_weapons(self):
        strong = SimpleNamespace(name='diamond_sword')
        weapons = [strong, SimpleNamespace(name='stone_sword'), SimpleNamespace(name='bow')]
        self.combat.equip_weapon(self.client, weapons, prefer_sword=True)
        self.client.equip.assert_called_once_with(strong, 'hand')

    def test_waits_for_recovery_after_swapping_weapons(self):
        self.combat.last_weapon_swap = 9.8
        self.combat.tick(self.client, self.target, 2.5, 10.0)
        self.client.attack.assert_not_called()

    def test_losing_target_releases_combat_inputs(self):
        self.client.clearControlStates.reset_mock()
        self.combat.tick(self.client, None, float('inf'), 10.0)
        self.client.clearControlStates.assert_called_once()

    def test_targets_nearest_spawned_non_spectator(self):
        self.client.entity.position = SimpleNamespace(x=0, y=0, z=0)
        opponent = SimpleNamespace(position=SimpleNamespace(x=3, y=0, z=0))
        closer = SimpleNamespace(position=SimpleNamespace(x=1, y=0, z=0))
        self.client.players = {
            'Opponent': SimpleNamespace(entity=opponent, gamemode=0),
            'Spectator': SimpleNamespace(entity=closer, gamemode=3),
            self.player.name: SimpleNamespace(entity=closer, gamemode=0),
            'Unspawned': SimpleNamespace(entity=None, gamemode=0),
        }
        self.assertEqual(self.combat.find_target(self.client), (opponent, 3.0))

    def test_mode_change_releases_combat_inputs(self):
        self.client.clearControlStates.reset_mock()
        self.player.set_mode(MODE_LOOT)
        self.client.clearControlStates.assert_called_once()


if __name__ == '__main__':
    unittest.main()
