import math


class Navigation:
    def __init__(self, client, plugin, retreat_hop=None):
        self.client = client
        self.pathfinder = client.pathfinder
        self.goals = plugin.goals
        self.retreat_hop = retreat_hop
        self.travel = self.make_movements(plugin, sprint=True)
        self.walk = self.make_movements(plugin, sprint=False)
        self.walking = False
        self.goal_key = None
        self.pathfinder.setMovements(self.travel)

    def make_movements(self, plugin, sprint):
        # JSPyBridge handles ES6 classes directly. Its .new alias can free a shared reference.
        movements = plugin.Movements(self.client)
        movements.canDig = False
        movements.canOpenDoors = False
        movements.scafoldingBlocks = []
        movements.allow1by1towers = False
        movements.allowParkour = False
        movements.allowSprinting = sprint
        movements.maxDropDown = 2
        return movements

    def has_goal(self, key):
        return key == self.goal_key and self.pathfinder.goal is not None

    def set_goal(self, key, goal, *, dynamic=False, walking=False):
        self.pathfinder.enablePathShortcut = key[0] == 'sprint_retreat'
        if self.walking != walking:
            self.pathfinder.setMovements(self.walk if walking else self.travel)
            self.walking = walking
        self.pathfinder.setGoal(goal, dynamic)
        self.goal_key = key

    def stop(self):
        if self.goal_key is not None:
            self.pathfinder.setGoal(None)
            self.pathfinder.enablePathShortcut = False
            self.goal_key = None

    def follow(self, target):
        key = ('follow', target.id)
        if not self.has_goal(key):
            self.set_goal(key, self.goals.GoalFollow(target, 1), dynamic=True)

    def approach_containers(self, positions, reach):
        key = ('containers', tuple((pos.x, pos.y, pos.z) for pos in positions))
        if self.has_goal(key):
            return
        goals = [
            self.goals.GoalLookAtBlock(pos, self.client.world, {'reach': reach})
            for pos in positions
        ]
        self.set_goal(key, self.goals.GoalCompositeAny(goals))

    def run_retreat_hops(self):
        if self.retreat_hop is not None:
            self.retreat_hop(self.client)

    def retreat(self, target, distance, *, sprint_jump=False):
        position = target.position
        x, y, z = position.x, position.y, position.z
        # Keep one route through the sprint so replanning does not interrupt takeoff.
        key = ('sprint_retreat', target.id) if sprint_jump else (
            'walk_retreat', target.id, math.floor(x / 2), math.floor(y / 2), math.floor(z / 2)
        )
        if not self.has_goal(key):
            near = self.goals.GoalNear(x, y, z, distance + 1)
            self.set_goal(key, self.goals.GoalInvert(near), walking=not sprint_jump)
