import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import bot as bot_module
from bot import Bot, MODE_COMBAT, MODE_LOOT
from combat import HEALING_HEALTH, HEALING_RETREAT_DISTANCE
from navigation import Navigation


class SpawnTests(unittest.TestCase):
    def test_spawns_one_bot_per_name_in_order(self):
        names = ['Cedar', 'River', 'Stone']
        with patch.multiple(bot_module, BOT_NAMES=names, bots=[]), \
                patch.object(Bot, 'start', autospec=True) as start, \
                patch('bot.time.sleep'), patch('bot.threading.Thread'), patch('bot.terminate'):
            bot_module.main()

        self.assertEqual([entry.args[0].name for entry in start.call_args_list], names)

    def test_empty_name_list_starts_no_bots_or_command_thread(self):
        with patch.multiple(bot_module, BOT_NAMES=[], bots=[]), \
                patch.object(Bot, 'start') as start, patch('bot.time.sleep'), \
                patch('bot.threading.Thread') as thread, patch('bot.terminate'):
            bot_module.main()

        start.assert_not_called()
        thread.assert_not_called()

    def test_console_mode_commands_apply_to_all_bots(self):
        roster = [Bot(name, 'localhost', 25565) for name in ('Cedar', 'River', 'Stone')]
        for key, expected in [('2', MODE_COMBAT), ('1', MODE_LOOT)]:
            with self.subTest(key=key), patch('bot.bots', roster), \
                    patch('bot.sys.platform', 'other'), patch('bot.sys.stdin.read', side_effect=[key, '']):
                bot_module.read_commands()

            self.assertEqual([current.mode for current in roster], [expected] * len(roster))


class SwordFightTests(unittest.TestCase):
    def setUp(self):
        self.player = Bot('Cedar', 'localhost', 25565, difficulty='standard')
        self.client = self.player.bot = Mock()
        self.combat = self.player.combat
        self.client.health = 20
        self.client.usingHeldItem = False
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

    def test_combat_ignores_listed_bots_by_exact_name(self):
        with patch('bot.BOT_NAMES', ['Cedar', 'River']):
            player = Bot('Cedar', 'localhost', 25565, difficulty='standard')
        self.client.entity.position = SimpleNamespace(x=0, y=0, z=0)
        teammate = SimpleNamespace(position=SimpleNamespace(x=1, y=0, z=0))
        opponent = SimpleNamespace(position=SimpleNamespace(x=2, y=0, z=0))
        self.client.players = {
            'River': SimpleNamespace(entity=teammate, gamemode=0),
            'RiverRunner': SimpleNamespace(entity=opponent, gamemode=0),
        }

        self.assertEqual(player.combat.find_target(self.client), (opponent, 2.0))

    def test_mode_change_releases_combat_inputs(self):
        self.client.clearControlStates.reset_mock()
        self.player.set_mode(MODE_LOOT)
        self.client.clearControlStates.assert_called_once()

    def test_healing_prefers_enchanted_apple_below_healing_threshold(self):
        self.client.health = HEALING_HEALTH - 1
        regular = SimpleNamespace(name='golden_apple')
        enchanted = SimpleNamespace(name='enchanted_golden_apple')

        self.assertTrue(self.combat.consume_healing_apple(self.client, [regular, enchanted]))

        self.client.equip.assert_called_once_with(enchanted, 'hand')
        self.client.consume.assert_called_once_with()

    def test_does_not_heal_at_threshold(self):
        self.client.health = HEALING_HEALTH
        apple = SimpleNamespace(name='golden_apple')

        self.assertFalse(self.combat.consume_healing_apple(self.client, [apple]))

        self.client.consume.assert_not_called()

    def test_low_health_without_apples_leaves_hunger_fallback_available(self):
        self.client.health = HEALING_HEALTH - 1
        bread = SimpleNamespace(name='bread')

        self.assertFalse(self.combat.consume_healing_apple(self.client, [bread]))

        self.client.equip.assert_not_called()

    def test_retreats_from_opponent_during_apple_consumption(self):
        self.client.health = HEALING_HEALTH - 1
        position = self.client.entity.position
        position.x, position.z = 0, 0
        self.target.position.x, self.target.position.z = 2.5, 0
        apple = SimpleNamespace(name='golden_apple')

        def while_eating():
            self.combat.tick(self.client, self.target, 2.5, 10.0)
            position.offset.assert_called_once_with(-2.5, 1.62, 0)
            self.client.lookAt.assert_called_once_with(position.offset.return_value, True)
            self.client.setControlState.assert_called_once_with('forward', True)
            self.client.attack.assert_not_called()
            self.client.clearControlStates.reset_mock()

        self.client.consume.side_effect = while_eating
        self.assertTrue(self.combat.consume_healing_apple(self.client, [apple]))

        self.client.equip.assert_called_once_with(apple, 'hand')
        self.client.clearControlStates.assert_called_once_with()
        self.assertFalse(self.combat.healing)

    def test_stops_at_safe_distance_while_eating(self):
        self.client.health = HEALING_HEALTH - 1
        apple = SimpleNamespace(name='golden_apple')

        def while_eating():
            self.combat.tick(self.client, self.target, HEALING_RETREAT_DISTANCE, 10.0)
            self.client.setControlState.assert_called_once_with('forward', False)
            self.client.lookAt.assert_not_called()

        self.client.consume.side_effect = while_eating
        self.assertTrue(self.combat.consume_healing_apple(self.client, [apple]))

    def test_consume_failure_releases_retreat_and_hand(self):
        self.client.health = HEALING_HEALTH - 1
        self.client.usingHeldItem = True
        apple = SimpleNamespace(name='golden_apple')
        self.client.consume.side_effect = RuntimeError('consume timed out')
        self.client.clearControlStates.reset_mock()

        with self.assertRaisesRegex(RuntimeError, 'consume timed out'):
            self.combat.consume_healing_apple(self.client, [apple])

        self.client.deactivateItem.assert_called_once_with()
        self.client.clearControlStates.assert_called_with()
        self.assertFalse(self.combat.healing)
        self.assertFalse(self.combat.hand_lock.locked())

    def test_pathfinder_owns_approach_until_melee_range(self):
        navigation = Mock()
        self.combat.tick(self.client, self.target, 4.0, 10.0, navigation)
        navigation.follow.assert_called_once_with(self.target)
        self.client.setControlState.assert_not_called()

        self.combat.tick(self.client, self.target, 2.5, 10.1, navigation)
        navigation.stop.assert_called_once_with()
        self.client.attack.assert_called_once_with(self.target)

    def test_pathfinder_routes_around_a_wall_even_within_melee_range(self):
        navigation = Mock()
        self.client.world.raycast.return_value = object()

        self.combat.tick(self.client, self.target, 2.5, 10.0, navigation)

        navigation.follow.assert_called_once_with(self.target)
        self.client.attack.assert_not_called()

    def test_pathfinder_owns_healing_retreat_until_eating_ends(self):
        self.client.health = HEALING_HEALTH - 1
        apple = SimpleNamespace(name='golden_apple')
        navigation = Mock()
        self.client.consume.side_effect = lambda: self.combat.tick(
            self.client, self.target, 2.5, 10.0, navigation
        )

        self.assertTrue(self.combat.consume_healing_apple(self.client, [apple], navigation))
        self.assertEqual(navigation.method_calls, [
            call.stop(), call.retreat(self.target, HEALING_RETREAT_DISTANCE, sprint_jump=False), call.stop(),
        ])
        self.client.attack.assert_not_called()

    def test_sprint_jump_retreat_happens_before_the_apple_bite(self):
        self.client.health = HEALING_HEALTH - 1
        self.client.entity.position = SimpleNamespace(x=0, y=0, z=0)
        target = SimpleNamespace(id=2, position=SimpleNamespace(x=2.5, y=0, z=0))
        self.client.players = {'Opponent': SimpleNamespace(entity=target, gamemode=0)}
        navigation = Mock()
        navigation.run_retreat_hops.side_effect = lambda: self.combat.tick(
            self.client, target, 2.5, 10.0, navigation
        )
        self.client.consume.side_effect = lambda: self.combat.tick(
            self.client, target, 2.5, 10.1, navigation
        )

        apple = SimpleNamespace(name='golden_apple')
        self.assertTrue(self.combat.consume_healing_apple(
            self.client, [apple], navigation, combat_mode=True
        ))

        self.assertEqual(navigation.method_calls, [
            call.stop(),
            call.run_retreat_hops(),
            call.retreat(target, HEALING_RETREAT_DISTANCE, sprint_jump=True),
            call.stop(),
            call.retreat(target, HEALING_RETREAT_DISTANCE, sprint_jump=False),
            call.stop(),
        ])


class ExpertMeleeTests(unittest.TestCase):
    def setUp(self):
        self.combat = Bot('Cedar', 'localhost', 25565, difficulty='expert').combat
        self.combat.swing_reach = 2.8
        self.client = Mock()
        self.client.health = 20
        self.client.heldItem = SimpleNamespace(name='iron_sword')
        self.client.entity.position.x = 0
        self.client.entity.position.z = 0
        self.client.world.raycast.return_value = None
        self.target = SimpleNamespace(id=42, height=1.8, position=Mock(x=2.5, z=0))
        self.aim = self.target.position.offset.return_value
        self.aim.minus.return_value.norm.return_value = 2.5

    def test_reacts_one_tick_after_a_valid_swing_opens(self):
        self.combat.tick(self.client, self.target, 2.5, 10.0)
        self.client.attack.assert_not_called()

        self.combat.tick(self.client, self.target, 2.5, 10.05)
        self.client.attack.assert_called_once_with(self.target)

    def test_blocked_attack_restarts_the_reaction_window(self):
        self.combat.tick(self.client, self.target, 2.5, 10.0)
        self.client.world.raycast.return_value = object()
        self.combat.tick(self.client, self.target, 2.5, 10.05)
        self.client.world.raycast.return_value = None

        self.combat.tick(self.client, self.target, 2.5, 10.1)
        self.client.attack.assert_not_called()

        self.combat.tick(self.client, self.target, 2.5, 10.16)
        self.client.attack.assert_called_once_with(self.target)

    def test_moves_into_its_sampled_reach_before_swinging(self):
        self.combat.swing_reach = 2.4
        self.combat.tick(self.client, self.target, 2.5, 10.0)
        self.combat.tick(self.client, self.target, 2.5, 10.1)

        self.client.attack.assert_not_called()
        self.client.setControlState.assert_any_call('forward', True)
        angle_offset = self.aim.offset.call_args.args[2]
        self.assertAlmostEqual(abs(angle_offset), 1.0101, places=4)

    def test_keeps_pursuing_a_fleeing_opponent_during_release(self):
        self.combat.last_attack = 10.0
        self.target.position.x = 2.6
        self.combat.tick(self.client, self.target, 2.6, 10.01)
        self.client.setControlState.reset_mock()

        self.target.position.x = 2.8
        self.combat.tick(self.client, self.target, 2.8, 10.06)

        self.client.setControlState.assert_any_call('forward', True)
        self.client.setControlState.assert_any_call('sprint', True)
        self.client.attack.assert_not_called()

    def test_backsteps_during_release_if_opponent_closes_in(self):
        self.combat.last_attack = 10.0
        self.target.position.x = 2.6
        self.combat.tick(self.client, self.target, 2.6, 10.01)
        self.client.setControlState.reset_mock()

        self.target.position.x = 2.4
        self.combat.tick(self.client, self.target, 2.4, 10.06)

        self.client.setControlState.assert_any_call('back', True)
        self.client.setControlState.assert_any_call('forward', False)


class NavigationTests(unittest.TestCase):
    def test_container_goals_use_the_class_proxy_directly(self):
        client = Mock()
        plugin = Mock()
        navigation = Navigation(client, plugin)
        position = SimpleNamespace(x=3, y=1, z=2)

        navigation.approach_containers([position], 4)

        plugin.goals.GoalLookAtBlock.assert_called_once_with(position, client.world, {'reach': 4})
        plugin.goals.GoalCompositeAny.assert_called_once_with([plugin.goals.GoalLookAtBlock.return_value])
        client.pathfinder.setGoal.assert_called_once_with(plugin.goals.GoalCompositeAny.return_value, False)

    def test_follow_keeps_the_same_dynamic_goal_until_stopped(self):
        client = Mock()
        plugin = Mock()
        navigation = Navigation(client, plugin)
        target = SimpleNamespace(id=42)
        goal = plugin.goals.GoalFollow.return_value
        client.pathfinder.goal = goal

        navigation.follow(target)
        navigation.follow(target)
        client.pathfinder.setGoal.assert_called_once_with(goal, True)

        navigation.stop()
        client.pathfinder.setGoal.assert_called_with(None)

    def test_retreat_switches_from_sprint_to_walk(self):
        client = Mock()
        plugin = Mock()
        plugin.Movements.side_effect = [Mock(), Mock()]
        navigation = Navigation(client, plugin)
        target = SimpleNamespace(id=42, position=SimpleNamespace(x=2, y=0, z=0))

        navigation.retreat(target, 12.0, sprint_jump=True)
        self.assertTrue(navigation.travel.allowSprinting)
        self.assertTrue(client.pathfinder.enablePathShortcut)
        client.pathfinder.setMovements.assert_called_once_with(navigation.travel)

        target.position.x = 5
        navigation.retreat(target, 12.0, sprint_jump=True)
        client.pathfinder.setGoal.assert_called_once()

        navigation.retreat(target, 12.0, sprint_jump=False)
        self.assertFalse(navigation.walk.allowSprinting)
        self.assertFalse(client.pathfinder.enablePathShortcut)
        client.pathfinder.setMovements.assert_called_with(navigation.walk)


if __name__ == '__main__':
    unittest.main()
