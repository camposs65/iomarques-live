import math
import random
import time
import tkinter as tk


class RogueBrechoGame(tk.Toplevel):
    def __init__(self, master, colors):
        super().__init__(master)
        self.colors = colors
        self.title("Roguelike da Peca 777 - IoMarques Brecho")
        self.geometry("980x720")
        self.minsize(920, 660)
        self.configure(bg=colors["app_bg"])
        self.transient(master)

        self.width = 900
        self.height = 560
        self.view_top = 104
        self.iso_x = 0.70
        self.iso_y = 0.38
        self.depth_x = 0.18
        self.depth_y = 0.48
        self.keys = set()
        self.mouse = {
            "x": self.width / 2,
            "y": self.height / 2,
            "screen_x": self.width / 2,
            "screen_y": self.height / 2,
            "down": False,
        }
        self.loop_job = None
        self.last_time = time.perf_counter()

        self.weapon_order = ["laser", "cabide", "pix", "tesoura"]
        self.weapon_data = {
            "laser": {
                "name": "Agulha Laser",
                "hint": "tiro rapido e preciso",
                "cooldown": 0.22,
                "color": "#F9A8D4",
            },
            "cabide": {
                "name": "Cabide Bumerangue",
                "hint": "lamina larga que atravessa",
                "cooldown": 0.55,
                "color": "#A7F3D0",
            },
            "pix": {
                "name": "Pix Bomba",
                "hint": "explosao em area",
                "cooldown": 0.85,
                "color": "#FDE68A",
            },
            "tesoura": {
                "name": "Tesoura Tripla",
                "hint": "rajada em leque",
                "cooldown": 0.48,
                "color": "#93C5FD",
            },
        }

        self.power_pool = [
            {
                "name": "Etiqueta Flamejante",
                "desc": "+25% dano em todas as armas.",
                "apply": lambda: self._add_stat("damage", 0.25),
            },
            {
                "name": "Live Acelerada",
                "desc": "+18% velocidade e esquiva.",
                "apply": lambda: self._add_stat("speed", 0.18),
            },
            {
                "name": "Pix Relampago",
                "desc": "Armas recarregam 18% mais rapido.",
                "apply": lambda: self._add_stat("fire_rate", 0.18),
            },
            {
                "name": "Arara Blindada",
                "desc": "+25 vida maxima e cura junto.",
                "apply": lambda: self._increase_max_hp(25),
            },
            {
                "name": "Garimpo Duplo",
                "desc": "Laser e tesoura disparam projeteis extras.",
                "apply": lambda: self._add_stat("multishot", 1),
            },
            {
                "name": "Cupom de Sorte",
                "desc": "Mais powerups caem dos inimigos.",
                "apply": lambda: self._add_stat("luck", 0.35),
            },
            {
                "name": "Orbe de Seda",
                "desc": "Um orbe gira e fere inimigos proximos.",
                "apply": lambda: self._add_stat("orbs", 1),
            },
            {
                "name": "Vitrine Congelante",
                "desc": "Inimigos ficam 15% mais lentos.",
                "apply": lambda: self._add_stat("slow", 0.15),
            },
            {
                "name": "Sacola Magnetica",
                "desc": "Powerups e moedas vem ate voce.",
                "apply": lambda: self._add_stat("magnet", 60),
            },
        ]

        self.header = tk.Frame(self, bg=self.colors["primary"])
        self.header.pack(fill="x")
        tk.Label(
            self.header,
            text="Roguelike da Peca 777",
            bg=self.colors["primary"],
            fg="#FFFFFF",
            font=("Segoe UI Semibold", 20),
        ).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(
            self.header,
            text="WASD/setas movem | mouse mira | 1-4 troca arma | clique/auto dispara | P pausa | R reinicia",
            bg=self.colors["primary"],
            fg=self.colors["app_bg"],
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=18, pady=(0, 14))

        self.hud = tk.Frame(self, bg=self.colors["app_bg"])
        self.hud.pack(fill="x", padx=18, pady=(12, 8))
        self.room_var = tk.StringVar()
        self.hp_var = tk.StringVar()
        self.weapon_var = tk.StringVar()
        self.score_var = tk.StringVar()
        for variable in (self.room_var, self.hp_var, self.weapon_var, self.score_var):
            tk.Label(
                self.hud,
                textvariable=variable,
                bg=self.colors["timer_bg"],
                fg=self.colors["text"],
                font=("Segoe UI Semibold", 10),
                padx=12,
                pady=6,
            ).pack(side="left", padx=(0, 8))

        tk.Button(
            self.hud,
            text="Reiniciar run",
            command=self.reset_run,
            bg=self.colors["secondary"],
            fg=self.colors["button_text"],
            activebackground="#E3C6E3",
            activeforeground=self.colors["button_text"],
            relief="flat",
            bd=0,
            padx=12,
            pady=6,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        ).pack(side="right")

        self.canvas = tk.Canvas(
            self,
            width=self.width,
            height=self.height,
            bg="#130C1B",
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.canvas.focus_set()

        self.bind("<KeyPress>", self._on_key_press)
        self.bind("<KeyRelease>", self._on_key_release)
        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<Button-1>", lambda _event: self.canvas.focus_set())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.reset_run()
        self.loop_job = self.after(16, self._game_loop)

    def reset_run(self):
        self.room = 1
        self.score = 0
        self.coins = 0
        self.selected_weapon = "laser"
        self.last_shot_at = {weapon: -100.0 for weapon in self.weapon_order}
        self.game_time = 0.0
        self.paused = False
        self.game_over = False
        self.victory = False
        self.power_choices = None
        self.message = (
            "A Peca 777 caiu no estoque infinito. Sobreviva as salas, escolha poderes e derrote os bosses."
        )
        self.message_timer = 5.0
        self.player = {
            "x": self.width / 2,
            "y": self.height / 2,
            "r": 16,
            "hp": 120,
            "max_hp": 120,
            "speed": 250,
            "invuln": 1.4,
            "shield": 0,
        }
        self.stats = {
            "damage": 1.0,
            "speed": 1.0,
            "fire_rate": 1.0,
            "multishot": 0,
            "luck": 0.0,
            "orbs": 0,
            "slow": 0.0,
            "magnet": 90,
        }
        self.powers_taken = []
        self.projectiles = []
        self.enemy_projectiles = []
        self.enemies = []
        self.pickups = []
        self.particles = []
        self.obstacles = []
        self.room_clear_timer = 0.0
        self._start_room()

    def _start_room(self):
        self.projectiles.clear()
        self.enemy_projectiles.clear()
        self.pickups.clear()
        self.particles.clear()
        self.power_choices = None
        self.room_clear_timer = 0.0
        self.player["x"] = self.width / 2
        self.player["y"] = self.height / 2
        self.player["invuln"] = 1.2
        self.obstacles = self._generate_obstacles()
        self._place_player_safely()
        self.enemies = []

        if self.room % 4 == 0:
            self._spawn_boss()
            self.message = self.enemies[0]["intro"]
            self.message_timer = 5.0
        else:
            self._spawn_wave()
            self.message = f"Sala {self.room}: garimpe, sobreviva e pegue os powerups."
            self.message_timer = 3.0
        self._refresh_hud()

    def _generate_obstacles(self):
        layouts = [
            [(194, 172, 308, 210), (592, 172, 706, 210), (372, 342, 528, 380)],
            [(136, 124, 176, 354), (724, 206, 764, 436), (342, 252, 558, 292)],
            [(210, 122, 310, 162), (590, 398, 690, 438), (392, 190, 432, 370)],
            [(156, 406, 360, 442), (540, 116, 744, 152), (426, 274, 474, 322)],
        ]
        safe_zones = self._safe_spawn_zones()
        obstacles = []
        for rect in random.choice(layouts):
            inflated = self._inflate_rect(rect, 24)
            if any(self._rects_overlap(inflated, zone) for zone in safe_zones):
                continue
            obstacles.append(rect)
        return obstacles

    def _safe_spawn_zones(self):
        return [
            (self.width / 2 - 100, self.height / 2 - 90, self.width / 2 + 100, self.height / 2 + 90),
            (42, 42, 150, 150),
            (self.width - 150, 42, self.width - 42, 150),
            (42, self.height - 150, 150, self.height - 42),
            (self.width - 150, self.height - 150, self.width - 42, self.height - 42),
        ]

    def _place_player_safely(self):
        x, y = self._free_point_near(self.width / 2, self.height / 2, self.player["r"] + 10)
        self.player["x"] = x
        self.player["y"] = y

    def _free_point_near(self, target_x, target_y, radius):
        candidates = [(target_x, target_y)]
        for distance in range(40, 301, 32):
            for step in range(16):
                angle = step * math.tau / 16
                candidates.append((target_x + math.cos(angle) * distance, target_y + math.sin(angle) * distance))
        for x, y in candidates:
            x = max(radius, min(self.width - radius, x))
            y = max(radius, min(self.height - radius, y))
            if not self._hits_obstacle(x, y, radius):
                return x, y
        return self.width / 2, self.height / 2

    def _spawn_wave(self):
        budget = 4 + self.room * 2
        enemy_types = ["runner", "spitter", "brute", "dasher", "mender"]
        while budget > 0:
            kind = random.choices(enemy_types, weights=[36, 24, 16, 16, 8], k=1)[0]
            cost = {"runner": 1, "spitter": 2, "brute": 3, "dasher": 2, "mender": 3}[kind]
            if cost > budget and budget > 1:
                kind = "runner"
                cost = 1
            self.enemies.append(self._make_enemy(kind))
            budget -= cost

    def _spawn_boss(self):
        boss_index = (self.room // 4 - 1) % 3
        bosses = [
            {
                "kind": "boss_queen",
                "name": "Rainha das Sombras",
                "intro": "Boss: Rainha das Sombras. Ela dispara coroas em circulo e invoca sombras pequenas.",
                "hp": 560 + self.room * 45,
                "color": "#B65AC0",
                "r": 42,
            },
            {
                "kind": "boss_mannequin",
                "name": "Manequim Blindado",
                "intro": "Boss: Manequim Blindado. Ele investe, bate forte e deixa vitrines pelo caminho.",
                "hp": 720 + self.room * 55,
                "color": "#93C5FD",
                "r": 46,
            },
            {
                "kind": "boss_dragon",
                "name": "Dragao das Etiquetas",
                "intro": "Boss: Dragao das Etiquetas. Desvie dos espirais e das bombas de preco.",
                "hp": 640 + self.room * 52,
                "color": "#F59E0B",
                "r": 44,
            },
        ]
        data = bosses[boss_index]
        enemy = self._make_enemy(data["kind"])
        enemy.update(data)
        enemy["max_hp"] = data["hp"]
        enemy["hp"] = data["hp"]
        enemy["x"] = self.width / 2
        enemy["y"] = 126
        enemy["boss"] = True
        self.enemies.append(enemy)

    def _make_enemy(self, kind):
        x, y = self._spawn_point()
        templates = {
            "runner": ("Sombra Rapida", 42, 15, 112, "#8B5CF6"),
            "spitter": ("Mariposa Pix", 58, 16, 72, "#38BDF8"),
            "brute": ("Sacola Bruta", 120, 23, 54, "#F97316"),
            "dasher": ("Cabide Veloz", 70, 17, 84, "#F43F5E"),
            "mender": ("Costureira Sombria", 68, 17, 58, "#22C55E"),
            "boss_queen": ("Rainha das Sombras", 600, 42, 42, "#B65AC0"),
            "boss_mannequin": ("Manequim Blindado", 760, 46, 48, "#93C5FD"),
            "boss_dragon": ("Dragao das Etiquetas", 680, 44, 52, "#F59E0B"),
        }
        name, hp, radius, speed, color = templates[kind]
        hp += self.room * (7 if not kind.startswith("boss") else 0)
        return {
            "kind": kind,
            "name": name,
            "x": x,
            "y": y,
            "vx": 0.0,
            "vy": 0.0,
            "hp": hp,
            "max_hp": hp,
            "r": radius,
            "speed": speed,
            "color": color,
            "cooldown": random.uniform(0.4, 1.5),
            "phase": random.random() * math.tau,
            "stun": 0.0,
            "boss": kind.startswith("boss"),
            "stuck": 0.0,
            "last_x": x,
            "last_y": y,
        }

    def _spawn_point(self):
        for _attempt in range(200):
            side = random.choice(["top", "bottom", "left", "right"])
            margin = 62
            if side == "top":
                x, y = random.uniform(margin, self.width - margin), random.uniform(margin, 110)
            elif side == "bottom":
                x, y = random.uniform(margin, self.width - margin), random.uniform(self.height - 110, self.height - margin)
            elif side == "left":
                x, y = random.uniform(margin, 130), random.uniform(margin, self.height - margin)
            else:
                x, y = random.uniform(self.width - 130, self.width - margin), random.uniform(margin, self.height - margin)
            if self._distance(x, y, self.player["x"], self.player["y"]) > 230 and not self._hits_obstacle(x, y, 26):
                return x, y
        for x, y in [(70, 70), (830, 70), (70, 490), (830, 490), (self.width / 2, 78), (self.width / 2, self.height - 78)]:
            if self._distance(x, y, self.player["x"], self.player["y"]) > 170 and not self._hits_obstacle(x, y, 26):
                return x, y
        return self._free_point_near(self.width / 2, 86, 28)

    def _on_key_press(self, event):
        key = event.keysym.lower()
        if self.power_choices and key in {"1", "2", "3"}:
            self._choose_power(int(key) - 1)
            return
        if key in {"1", "2", "3", "4"}:
            self.selected_weapon = self.weapon_order[int(key) - 1]
            self._show_message(f"Arma: {self.weapon_data[self.selected_weapon]['name']}", 1.4)
            return
        if key == "q":
            self._cycle_weapon(-1)
            return
        if key == "e":
            self._cycle_weapon(1)
            return
        if key == "p":
            if not self.power_choices and not self.game_over:
                self.paused = not self.paused
            return
        if key == "r":
            self.reset_run()
            return
        if key in {"space", "return"}:
            if self.power_choices:
                self._choose_power(0)
            elif self.game_over or self.victory:
                self.reset_run()
            return
        if key in {"up", "down", "left", "right", "w", "a", "s", "d"}:
            self.keys.add(key)

    def _on_key_release(self, event):
        self.keys.discard(event.keysym.lower())

    def _on_mouse_move(self, event):
        self.mouse["screen_x"] = event.x
        self.mouse["screen_y"] = event.y
        self.mouse["x"], self.mouse["y"] = self._screen_to_world(event.x, event.y)

    def _on_mouse_down(self, event):
        self.mouse["down"] = True
        self.mouse["screen_x"] = event.x
        self.mouse["screen_y"] = event.y
        self.mouse["x"], self.mouse["y"] = self._screen_to_world(event.x, event.y)
        if self.power_choices:
            self._click_power_card(event.x, event.y)

    def _on_mouse_up(self, _event):
        self.mouse["down"] = False

    def _cycle_weapon(self, direction):
        index = self.weapon_order.index(self.selected_weapon)
        self.selected_weapon = self.weapon_order[(index + direction) % len(self.weapon_order)]
        self._show_message(f"Arma: {self.weapon_data[self.selected_weapon]['name']}", 1.4)

    def _game_loop(self):
        now = time.perf_counter()
        dt = min(0.035, max(0.001, now - self.last_time))
        self.last_time = now
        if not self.paused and not self.power_choices and not self.game_over and not self.victory:
            self.game_time += dt
            self._update(dt)
        self._draw()
        self.loop_job = self.after(16, self._game_loop)

    def _update(self, dt):
        if self.message_timer > 0:
            self.message_timer -= dt
        if self.player["invuln"] > 0:
            self.player["invuln"] -= dt
        if self.player["shield"] > 0:
            self.player["shield"] -= dt

        self._move_player(dt)
        self._auto_fire()
        self._update_orbs(dt)
        self._update_projectiles(dt)
        self._update_enemies(dt)
        self._update_pickups(dt)
        self._update_particles(dt)

        if not self.enemies and self.room_clear_timer <= 0:
            self.room_clear_timer = 0.75
        if self.room_clear_timer > 0:
            self.room_clear_timer -= dt
            if self.room_clear_timer <= 0:
                self._open_power_choice()

    def _move_player(self, dt):
        speed = self.player["speed"] * self.stats["speed"]
        dx = 0.0
        dy = 0.0
        if self.keys.intersection({"left", "a"}):
            dx -= 1
        if self.keys.intersection({"right", "d"}):
            dx += 1
        if self.keys.intersection({"up", "w"}):
            dy -= 1
        if self.keys.intersection({"down", "s"}):
            dy += 1
        if dx and dy:
            dx *= 0.707
            dy *= 0.707
        self._try_move_player(dx * speed * dt, 0)
        self._try_move_player(0, dy * speed * dt)

    def _try_move_player(self, dx, dy):
        x = max(self.player["r"], min(self.width - self.player["r"], self.player["x"] + dx))
        y = max(self.player["r"], min(self.height - self.player["r"], self.player["y"] + dy))
        if not self._hits_obstacle(x, y, self.player["r"]):
            self.player["x"] = x
            self.player["y"] = y

    def _auto_fire(self):
        if not self.enemies:
            return
        target_x, target_y = self._aim_point()
        weapon = self.selected_weapon
        cooldown = self.weapon_data[weapon]["cooldown"] / max(0.35, self.stats["fire_rate"])
        if self.game_time - self.last_shot_at[weapon] >= cooldown or self.mouse["down"]:
            if self.game_time - self.last_shot_at[weapon] >= cooldown * (0.55 if self.mouse["down"] else 1.0):
                self.last_shot_at[weapon] = self.game_time
                self._fire_weapon(weapon, target_x, target_y)

    def _aim_point(self):
        if 0 <= self.mouse["x"] <= self.width and 0 <= self.mouse["y"] <= self.height:
            return self.mouse["x"], self.mouse["y"]
        nearest = min(
            self.enemies,
            key=lambda enemy: self._distance(enemy["x"], enemy["y"], self.player["x"], self.player["y"]),
        )
        return nearest["x"], nearest["y"]

    def _fire_weapon(self, weapon, target_x, target_y):
        px, py = self.player["x"], self.player["y"]
        angle = math.atan2(target_y - py, target_x - px)
        damage = self.stats["damage"]
        extra = int(self.stats["multishot"])
        if weapon == "laser":
            spread = [0] + ([-0.16, 0.16] if extra else [])
            for offset in spread:
                self._spawn_player_projectile(angle + offset, 690, 22 * damage, 6, "#F9A8D4", life=0.9)
        elif weapon == "cabide":
            for offset in (-0.24, 0, 0.24):
                self._spawn_player_projectile(angle + offset, 420, 31 * damage, 12, "#A7F3D0", life=1.15, pierce=2)
        elif weapon == "pix":
            self._spawn_player_projectile(angle, 360, 48 * damage, 11, "#FDE68A", life=1.15, explode=82)
        elif weapon == "tesoura":
            offsets = [-0.34, 0, 0.34]
            if extra:
                offsets.extend([-0.58, 0.58])
            for offset in offsets:
                self._spawn_player_projectile(angle + offset, 600, 18 * damage, 5, "#93C5FD", life=0.7)

    def _spawn_player_projectile(self, angle, speed, damage, radius, color, life=1.0, pierce=0, explode=0):
        self.projectiles.append(
            {
                "x": self.player["x"] + math.cos(angle) * 20,
                "y": self.player["y"] + math.sin(angle) * 20,
                "vx": math.cos(angle) * speed,
                "vy": math.sin(angle) * speed,
                "damage": damage,
                "r": radius,
                "color": color,
                "life": life,
                "pierce": pierce,
                "explode": explode,
                "kind": "player",
            }
        )

    def _update_orbs(self, dt):
        orb_count = int(self.stats["orbs"])
        if orb_count <= 0:
            return
        radius = 64
        for index in range(orb_count):
            angle = self.game_time * 3.2 + index * math.tau / orb_count
            ox = self.player["x"] + math.cos(angle) * radius
            oy = self.player["y"] + math.sin(angle) * radius
            for enemy in list(self.enemies):
                if self._distance(ox, oy, enemy["x"], enemy["y"]) < enemy["r"] + 15:
                    self._damage_enemy(enemy, 46 * dt * self.stats["damage"], "#F9A8D4")

    def _update_projectiles(self, dt):
        for bullet in list(self.projectiles):
            bullet["x"] += bullet["vx"] * dt
            bullet["y"] += bullet["vy"] * dt
            bullet["life"] -= dt
            if bullet["life"] <= 0:
                if bullet.get("explode"):
                    self._explode(bullet["x"], bullet["y"], bullet["explode"], bullet["damage"])
                self._remove_projectile(bullet)
                continue
            if self._hits_obstacle(bullet["x"], bullet["y"], bullet["r"]):
                if bullet.get("explode"):
                    self._explode(bullet["x"], bullet["y"], bullet["explode"], bullet["damage"])
                self._remove_projectile(bullet)
                continue
            for enemy in list(self.enemies):
                if self._distance(bullet["x"], bullet["y"], enemy["x"], enemy["y"]) < bullet["r"] + enemy["r"]:
                    self._damage_enemy(enemy, bullet["damage"], bullet["color"])
                    if bullet.get("explode"):
                        self._explode(bullet["x"], bullet["y"], bullet["explode"], bullet["damage"])
                        self._remove_projectile(bullet)
                        break
                    if bullet["pierce"] > 0:
                        bullet["pierce"] -= 1
                    else:
                        self._remove_projectile(bullet)
                        break

        for bullet in list(self.enemy_projectiles):
            bullet["x"] += bullet["vx"] * dt
            bullet["y"] += bullet["vy"] * dt
            bullet["life"] -= dt
            if bullet["life"] <= 0 or self._hits_obstacle(bullet["x"], bullet["y"], bullet["r"]):
                self._remove_enemy_projectile(bullet)
                continue
            if self._distance(bullet["x"], bullet["y"], self.player["x"], self.player["y"]) < bullet["r"] + self.player["r"]:
                self._hurt_player(bullet["damage"])
                self._remove_enemy_projectile(bullet)

    def _update_enemies(self, dt):
        slow = max(0.25, 1.0 - self.stats["slow"])
        for enemy in list(self.enemies):
            if enemy["hp"] <= 0:
                continue
            enemy["phase"] += dt
            enemy["cooldown"] -= dt
            kind = enemy["kind"]
            if kind == "runner":
                self._enemy_chase(enemy, dt, slow)
            elif kind == "spitter":
                self._enemy_keep_distance(enemy, dt, slow, 170)
                if enemy["cooldown"] <= 0:
                    self._enemy_shoot(enemy, 210, 12, "#38BDF8")
                    enemy["cooldown"] = random.uniform(1.0, 1.8)
            elif kind == "brute":
                self._enemy_chase(enemy, dt, slow * 0.72)
            elif kind == "dasher":
                if enemy["cooldown"] <= 0:
                    self._dash_enemy(enemy)
                    enemy["cooldown"] = random.uniform(1.2, 1.8)
                self._move_enemy(enemy, enemy["vx"] * dt * slow, enemy["vy"] * dt * slow)
                enemy["vx"] *= 0.985
                enemy["vy"] *= 0.985
            elif kind == "mender":
                self._enemy_keep_distance(enemy, dt, slow, 220)
                if enemy["cooldown"] <= 0:
                    self._heal_nearby_enemies(enemy)
                    enemy["cooldown"] = 2.6
            elif kind == "boss_queen":
                self._boss_queen(enemy, dt, slow)
            elif kind == "boss_mannequin":
                self._boss_mannequin(enemy, dt, slow)
            elif kind == "boss_dragon":
                self._boss_dragon(enemy, dt, slow)

            if self._distance(enemy["x"], enemy["y"], self.player["x"], self.player["y"]) < enemy["r"] + self.player["r"]:
                self._hurt_player(16 if not enemy["boss"] else 28)

    def _enemy_chase(self, enemy, dt, slow):
        angle = math.atan2(self.player["y"] - enemy["y"], self.player["x"] - enemy["x"])
        sway = math.sin(enemy["phase"] * 3) * 0.35
        self._move_enemy(
            enemy,
            math.cos(angle + sway) * enemy["speed"] * slow * dt,
            math.sin(angle + sway) * enemy["speed"] * slow * dt,
        )

    def _enemy_keep_distance(self, enemy, dt, slow, preferred):
        distance = max(1, self._distance(enemy["x"], enemy["y"], self.player["x"], self.player["y"]))
        angle = math.atan2(self.player["y"] - enemy["y"], self.player["x"] - enemy["x"])
        direction = -1 if distance < preferred else 0.55
        strafe = math.sin(enemy["phase"] * 2.2) * 0.9
        self._move_enemy(
            enemy,
            (math.cos(angle) * direction + math.cos(angle + math.pi / 2) * strafe) * enemy["speed"] * slow * dt,
            (math.sin(angle) * direction + math.sin(angle + math.pi / 2) * strafe) * enemy["speed"] * slow * dt,
        )

    def _move_enemy(self, enemy, dx, dy):
        old_x = enemy["x"]
        old_y = enemy["y"]
        candidates = [
            (dx, dy),
            (dx, 0),
            (0, dy),
            (-dy * 0.85, dx * 0.85),
            (dy * 0.85, -dx * 0.85),
            (dx * 0.45, dy * 0.45),
        ]
        moved = False
        for try_dx, try_dy in candidates:
            nx = max(enemy["r"], min(self.width - enemy["r"], old_x + try_dx))
            ny = max(enemy["r"], min(self.height - enemy["r"], old_y + try_dy))
            if not self._hits_obstacle(nx, ny, enemy["r"]):
                enemy["x"] = nx
                enemy["y"] = ny
                moved = True
                break

        movement = self._distance(enemy["x"], enemy["y"], enemy.get("last_x", old_x), enemy.get("last_y", old_y))
        if moved and movement > 0.6:
            enemy["stuck"] = 0.0
        else:
            enemy["stuck"] = enemy.get("stuck", 0.0) + 1
            enemy["vx"] *= -0.45
            enemy["vy"] *= -0.45
            if enemy["stuck"] > 12:
                self._unstick_enemy(enemy)
                enemy["stuck"] = 0.0
        enemy["last_x"] = enemy["x"]
        enemy["last_y"] = enemy["y"]

    def _unstick_enemy(self, enemy):
        target_angle = math.atan2(enemy["y"] - self.player["y"], enemy["x"] - self.player["x"])
        for distance in (28, 42, 58, 76, 96, 124):
            for offset in (0, math.pi / 2, -math.pi / 2, math.pi, 0.7, -0.7):
                angle = target_angle + offset
                x = max(enemy["r"], min(self.width - enemy["r"], enemy["x"] + math.cos(angle) * distance))
                y = max(enemy["r"], min(self.height - enemy["r"], enemy["y"] + math.sin(angle) * distance))
                if not self._hits_obstacle(x, y, enemy["r"]):
                    enemy["x"] = x
                    enemy["y"] = y
                    self._burst(x, y, "#FFFFFF", 5)
                    return

    def _dash_enemy(self, enemy):
        angle = math.atan2(self.player["y"] - enemy["y"], self.player["x"] - enemy["x"])
        enemy["vx"] = math.cos(angle) * 460
        enemy["vy"] = math.sin(angle) * 460
        self._burst(enemy["x"], enemy["y"], "#F43F5E", 10)

    def _enemy_shoot(self, enemy, speed, damage, color, angle_offset=0):
        angle = math.atan2(self.player["y"] - enemy["y"], self.player["x"] - enemy["x"]) + angle_offset
        self.enemy_projectiles.append(
            {
                "x": enemy["x"] + math.cos(angle) * enemy["r"],
                "y": enemy["y"] + math.sin(angle) * enemy["r"],
                "vx": math.cos(angle) * speed,
                "vy": math.sin(angle) * speed,
                "damage": damage,
                "r": 6,
                "color": color,
                "life": 4.2,
            }
        )

    def _heal_nearby_enemies(self, healer):
        self._burst(healer["x"], healer["y"], "#22C55E", 16)
        for enemy in self.enemies:
            if enemy is healer:
                continue
            if self._distance(healer["x"], healer["y"], enemy["x"], enemy["y"]) < 190:
                enemy["hp"] = min(enemy["max_hp"], enemy["hp"] + 28 + self.room * 2)

    def _boss_queen(self, enemy, dt, slow):
        self._enemy_keep_distance(enemy, dt, slow * 0.65, 250)
        if enemy["cooldown"] <= 0:
            for index in range(14):
                angle = index * math.tau / 14 + self.game_time * 0.45
                self._spawn_enemy_bullet(enemy, angle, 175, 13, "#D946EF")
            if len(self.enemies) < 8:
                self.enemies.append(self._make_enemy("runner"))
            enemy["cooldown"] = 2.0

    def _boss_mannequin(self, enemy, dt, slow):
        if enemy["cooldown"] <= 0:
            self._dash_enemy(enemy)
            if random.random() < 0.55 and len(self.obstacles) < 8:
                x = max(80, min(self.width - 160, enemy["x"] - 52))
                y = max(80, min(self.height - 80, enemy["y"] - 18))
                self._try_add_obstacle((x, y, x + 104, y + 36))
            enemy["cooldown"] = 1.65
        self._move_enemy(enemy, enemy["vx"] * dt * slow, enemy["vy"] * dt * slow)
        enemy["vx"] *= 0.988
        enemy["vy"] *= 0.988

    def _boss_dragon(self, enemy, dt, slow):
        angle = self.game_time * 0.7
        target_x = self.width / 2 + math.cos(angle) * 230
        target_y = self.height / 2 + math.sin(angle * 0.8) * 170
        self._move_enemy(enemy, (target_x - enemy["x"]) * dt * slow, (target_y - enemy["y"]) * dt * slow)
        if enemy["cooldown"] <= 0:
            for index in range(9):
                self._spawn_enemy_bullet(enemy, angle + index * math.tau / 9, 210, 14, "#F59E0B")
            if random.random() < 0.55:
                self._spawn_enemy_bullet(enemy, math.atan2(self.player["y"] - enemy["y"], self.player["x"] - enemy["x"]), 140, 24, "#FDE68A", radius=12)
            enemy["cooldown"] = 1.25

    def _spawn_enemy_bullet(self, enemy, angle, speed, damage, color, radius=6):
        self.enemy_projectiles.append(
            {
                "x": enemy["x"] + math.cos(angle) * enemy["r"],
                "y": enemy["y"] + math.sin(angle) * enemy["r"],
                "vx": math.cos(angle) * speed,
                "vy": math.sin(angle) * speed,
                "damage": damage,
                "r": radius,
                "color": color,
                "life": 5.0,
            }
        )

    def _update_pickups(self, dt):
        for pickup in list(self.pickups):
            pickup["life"] -= dt
            if pickup["life"] <= 0:
                self.pickups.remove(pickup)
                continue
            magnet = self.stats["magnet"]
            distance = self._distance(pickup["x"], pickup["y"], self.player["x"], self.player["y"])
            if distance < magnet:
                angle = math.atan2(self.player["y"] - pickup["y"], self.player["x"] - pickup["x"])
                pickup["x"] += math.cos(angle) * 260 * dt
                pickup["y"] += math.sin(angle) * 260 * dt
            if distance < self.player["r"] + pickup["r"]:
                self._collect_pickup(pickup)

    def _collect_pickup(self, pickup):
        kind = pickup["kind"]
        if kind == "heal":
            self.player["hp"] = min(self.player["max_hp"], self.player["hp"] + 28)
            self._show_message("Powerup: cura +28", 1.5)
        elif kind == "shield":
            self.player["shield"] = max(self.player["shield"], 4.0)
            self._show_message("Powerup: escudo temporario", 1.5)
        elif kind == "haste":
            self.stats["speed"] += 0.08
            self._show_message("Powerup: velocidade permanente", 1.5)
        elif kind == "coin":
            self.coins += 1
            self.score += 25
        elif kind == "focus":
            self.stats["fire_rate"] += 0.05
            self._show_message("Powerup: foco de disparo", 1.5)
        if pickup in self.pickups:
            self.pickups.remove(pickup)
        self._burst(pickup["x"], pickup["y"], pickup["color"], 14)
        self._refresh_hud()

    def _update_particles(self, dt):
        for particle in list(self.particles):
            particle["x"] += particle["vx"] * dt
            particle["y"] += particle["vy"] * dt
            particle["vy"] += 50 * dt
            particle["life"] -= dt
            if particle["life"] <= 0:
                self.particles.remove(particle)

    def _damage_enemy(self, enemy, damage, color):
        enemy["hp"] -= damage
        self._burst(enemy["x"], enemy["y"], color, 4)
        if enemy["hp"] <= 0 and enemy in self.enemies:
            self.score += 250 if enemy["boss"] else 45
            self._drop_loot(enemy)
            self._burst(enemy["x"], enemy["y"], enemy["color"], 24 if enemy["boss"] else 12)
            self.enemies.remove(enemy)
            self._refresh_hud()

    def _explode(self, x, y, radius, damage):
        self._burst(x, y, "#FDE68A", 30)
        for enemy in list(self.enemies):
            distance = self._distance(x, y, enemy["x"], enemy["y"])
            if distance < radius + enemy["r"]:
                self._damage_enemy(enemy, damage * max(0.35, 1 - distance / (radius + enemy["r"])), "#FDE68A")

    def _hurt_player(self, amount):
        if self.player["invuln"] > 0:
            return
        if self.player["shield"] > 0:
            amount *= 0.35
            self.player["shield"] = max(0, self.player["shield"] - 0.75)
        self.player["hp"] -= amount
        self.player["invuln"] = 0.85
        self._burst(self.player["x"], self.player["y"], "#FCA5A5", 18)
        if self.player["hp"] <= 0:
            self.player["hp"] = 0
            self.game_over = True
            self.message = "Run encerrada. Pressione R ou Espaco para tentar uma build nova."
            self.message_timer = 999
        self._refresh_hud()

    def _drop_loot(self, enemy):
        chance = 0.22 + self.stats["luck"]
        if enemy["boss"]:
            drops = ["heal", "shield", "coin", "coin", "focus"]
        elif random.random() > chance:
            return
        else:
            drops = ["heal", "shield", "haste", "coin", "focus"]
        kind = random.choice(drops)
        data = {
            "heal": ("+", "#EF4444", 9),
            "shield": ("S", "#60A5FA", 9),
            "haste": (">", "#A7F3D0", 9),
            "coin": ("$", "#FDE68A", 8),
            "focus": ("*", "#F9A8D4", 8),
        }[kind]
        self.pickups.append(
            {
                "x": enemy["x"],
                "y": enemy["y"],
                "kind": kind,
                "label": data[0],
                "color": data[1],
                "r": data[2],
                "life": 12.0,
            }
        )

    def _open_power_choice(self):
        if self.room >= 12:
            self.victory = True
            self.message = "Voce venceu o estoque infinito e recuperou a Peca 777 lendaria!"
            self.message_timer = 999
            return
        self.power_choices = random.sample(self.power_pool, 3)
        self.message = "Escolha um poder para continuar a run."
        self.message_timer = 999

    def _click_power_card(self, x, y):
        if not self.power_choices:
            return
        for index, rect in enumerate(self._power_card_rects()):
            if rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]:
                self._choose_power(index)
                return

    def _choose_power(self, index):
        if not self.power_choices:
            return
        choice = self.power_choices[min(index, len(self.power_choices) - 1)]
        choice["apply"]()
        self.powers_taken.append(choice["name"])
        self.power_choices = None
        self.room += 1
        self._start_room()

    def _add_stat(self, key, amount):
        self.stats[key] += amount

    def _increase_max_hp(self, amount):
        self.player["max_hp"] += amount
        self.player["hp"] = min(self.player["max_hp"], self.player["hp"] + amount)

    def _refresh_hud(self):
        self.room_var.set(f"Sala: {self.room}")
        self.hp_var.set(f"Vida: {int(self.player['hp'])}/{int(self.player['max_hp'])}")
        weapon = self.weapon_data[self.selected_weapon]
        self.weapon_var.set(f"Arma: {weapon['name']}")
        self.score_var.set(f"Score: {self.score}  Moedas: {self.coins}")

    def _show_message(self, text, seconds):
        self.message = text
        self.message_timer = seconds

    def _remove_projectile(self, bullet):
        if bullet in self.projectiles:
            self.projectiles.remove(bullet)

    def _remove_enemy_projectile(self, bullet):
        if bullet in self.enemy_projectiles:
            self.enemy_projectiles.remove(bullet)

    def _hits_obstacle(self, x, y, radius):
        return any(self._circle_rect(x, y, radius, rect) for rect in self.obstacles)

    def _try_add_obstacle(self, rect):
        expanded = self._inflate_rect(rect, 28)
        player_box = (
            self.player["x"] - self.player["r"] - 42,
            self.player["y"] - self.player["r"] - 42,
            self.player["x"] + self.player["r"] + 42,
            self.player["y"] + self.player["r"] + 42,
        )
        if self._rects_overlap(expanded, player_box):
            return False
        if any(self._rects_overlap(expanded, zone) for zone in self._safe_spawn_zones()):
            return False
        for enemy in self.enemies:
            enemy_box = (
                enemy["x"] - enemy["r"] - 16,
                enemy["y"] - enemy["r"] - 16,
                enemy["x"] + enemy["r"] + 16,
                enemy["y"] + enemy["r"] + 16,
            )
            if self._rects_overlap(expanded, enemy_box):
                return False
        self.obstacles.append(rect)
        return True

    def _inflate_rect(self, rect, amount):
        x1, y1, x2, y2 = rect
        return (x1 - amount, y1 - amount, x2 + amount, y2 + amount)

    def _rects_overlap(self, first, second):
        return not (
            first[2] < second[0]
            or first[0] > second[2]
            or first[3] < second[1]
            or first[1] > second[3]
        )

    def _circle_rect(self, x, y, radius, rect):
        x1, y1, x2, y2 = rect
        nearest_x = max(x1, min(x, x2))
        nearest_y = max(y1, min(y, y2))
        return self._distance(x, y, nearest_x, nearest_y) < radius

    def _distance(self, x1, y1, x2, y2):
        return math.hypot(x1 - x2, y1 - y2)

    def _burst(self, x, y, color, amount):
        for _index in range(amount):
            angle = random.uniform(0, math.tau)
            speed = random.uniform(60, 280)
            self.particles.append(
                {
                    "x": x,
                    "y": y,
                    "vx": math.cos(angle) * speed,
                    "vy": math.sin(angle) * speed,
                    "life": random.uniform(0.22, 0.75),
                    "color": color,
                    "size": random.uniform(2, 5),
                }
            )

    def _project(self, x, y, z=0):
        center_x = self.width / 2
        center_y = self.height / 2
        screen_x = center_x + (x - center_x) * self.iso_x - (y - center_y) * self.iso_y
        screen_y = self.view_top + (x - center_x) * self.depth_x + y * self.depth_y - z
        return screen_x, screen_y

    def _screen_to_world(self, screen_x, screen_y):
        center_x = self.width / 2
        center_y = self.height / 2
        a = screen_x - center_x
        b = screen_y - self.view_top - self.depth_y * center_y
        determinant = self.iso_x * self.depth_y + self.iso_y * self.depth_x
        x = center_x + (a * self.depth_y + self.iso_y * b) / determinant
        y = center_y + (-self.depth_x * a + self.iso_x * b) / determinant
        return max(0, min(self.width, x)), max(0, min(self.height, y))

    def _depth_scale(self, y):
        return 0.78 + max(0, min(1, y / self.height)) * 0.28

    def _draw_shadow(self, x, y, radius, strength="#08040B"):
        sx, sy = self._project(x, y, 0)
        scale = self._depth_scale(y)
        self.canvas.create_oval(
            sx - radius * scale * 1.35,
            sy - radius * scale * 0.38,
            sx + radius * scale * 1.35,
            sy + radius * scale * 0.38,
            fill=strength,
            outline="",
        )

    def _draw_iso_disc(self, x, y, radius, color, label="", height=28, outline="#FFFFFF"):
        sx, sy = self._project(x, y, 0)
        top_x, top_y = self._project(x, y, height)
        scale = self._depth_scale(y)
        rx = radius * scale
        ry = radius * scale * 0.72
        self._draw_shadow(x, y, radius)
        self.canvas.create_line(sx - rx * 0.75, sy, top_x - rx * 0.75, top_y, fill="#160B20", width=2)
        self.canvas.create_line(sx + rx * 0.75, sy, top_x + rx * 0.75, top_y, fill="#160B20", width=2)
        self.canvas.create_oval(
            top_x - rx,
            top_y - ry,
            top_x + rx,
            top_y + ry,
            fill=color,
            outline=outline,
            width=2,
        )
        self.canvas.create_arc(
            top_x - rx,
            top_y - ry,
            top_x + rx,
            top_y + ry,
            start=200,
            extent=140,
            outline="#1C1424",
            width=2,
            style="arc",
        )
        if label:
            self.canvas.create_text(top_x, top_y, text=label, fill="#1C1424", font=("Segoe UI Semibold", 12))

    def _draw_iso_box(self, rect, color="#6C366B", height=46):
        x1, y1, x2, y2 = rect
        base = [self._project(x1, y1), self._project(x2, y1), self._project(x2, y2), self._project(x1, y2)]
        top = [self._project(x1, y1, height), self._project(x2, y1, height), self._project(x2, y2, height), self._project(x1, y2, height)]
        self.canvas.create_polygon(*[coord for point in (base[1], base[2], top[2], top[1]) for coord in point], fill="#3D2145", outline="#B98ABC")
        self.canvas.create_polygon(*[coord for point in (base[2], base[3], top[3], top[2]) for coord in point], fill="#2C1835", outline="#B98ABC")
        self.canvas.create_polygon(*[coord for point in top for coord in point], fill=color, outline="#E3C6E3", width=2)
        self.canvas.create_line(*top[0], *top[2], fill="#F9A8D4")
        self.canvas.create_line(*top[1], *top[3], fill="#F9A8D4")

    def _draw_iso_diamond(self, x, y, radius, color, label=""):
        sx, sy = self._project(x, y, 32 + math.sin(self.game_time * 6 + x) * 5)
        scale = self._depth_scale(y)
        r = radius * scale
        self._draw_shadow(x, y, radius)
        self.canvas.create_polygon(
            sx,
            sy - r,
            sx + r,
            sy,
            sx,
            sy + r,
            sx - r,
            sy,
            fill=color,
            outline="#FFFFFF",
            width=2,
        )
        if label:
            self.canvas.create_text(sx, sy + r + 13, text=label, fill="#FFFFFF", font=("Segoe UI Semibold", 9))

    def _draw(self):
        self.canvas.delete("all")
        self._draw_background()
        self._draw_obstacles()
        self._draw_pickups()
        self._draw_projectiles()
        self._draw_enemies()
        self._draw_orbs()
        self._draw_player()
        self._draw_particles()
        self._draw_boss_bar()
        self._draw_weapon_bar()
        if self.message_timer > 0 or self.game_over or self.victory:
            self._draw_message()
        if self.paused:
            self._draw_center_panel("Pausado", "Pressione P para continuar.")
        if self.power_choices:
            self._draw_power_choice()
        if self.game_over:
            self._draw_center_panel("Run encerrada", "Pressione R ou Espaco para recomecar.")
        if self.victory:
            self._draw_center_panel("Peca 777 recuperada", "Voce dominou o estoque infinito. Pressione R para uma nova run.")

    def _draw_background(self):
        self.canvas.create_rectangle(0, 0, self.width, self.height, fill="#0F0818", outline="")
        for index in range(24):
            y1 = index * self.height / 24
            y2 = (index + 1) * self.height / 24 + 1
            color = self._blend("#12091B", "#3B1D46", index / 23)
            self.canvas.create_rectangle(0, y1, self.width, y2, fill=color, outline="")

        for y in range(0, self.height + 72, 72):
            left = self._project(0, y)
            right = self._project(self.width, y)
            self.canvas.create_line(*left, *right, fill="#3A2145", width=1)
        for x in range(0, self.width + 72, 72):
            top = self._project(x, 0)
            bottom = self._project(x, self.height)
            self.canvas.create_line(*top, *bottom, fill="#2A1833", width=1)

        arena = [self._project(0, 0), self._project(self.width, 0), self._project(self.width, self.height), self._project(0, self.height)]
        self.canvas.create_polygon(*[coord for point in arena for coord in point], outline="#D8B6D8", width=3, fill="")
        drift = self.game_time * 18
        for index in range(36):
            x = (index * 101 + drift) % (self.width + 140) - 70
            y = 38 + (index * 59) % (self.height - 70)
            sx, sy = self._project(x, y, 16 + index % 4)
            radius = 1 + index % 3
            self.canvas.create_oval(sx - radius, sy - radius, sx + radius, sy + radius, fill="#F9D8F9", outline="")

    def _draw_obstacles(self):
        for rect in sorted(self.obstacles, key=lambda item: item[3]):
            self._draw_iso_box(rect, color="#6C366B", height=46)

    def _draw_pickups(self):
        for pickup in self.pickups:
            self._draw_iso_diamond(pickup["x"], pickup["y"], pickup["r"] * 1.35, pickup["color"], pickup["label"])

    def _draw_projectiles(self):
        for bullet in self.projectiles:
            sx, sy = self._project(bullet["x"], bullet["y"], 24)
            r = max(3, bullet["r"] * self._depth_scale(bullet["y"]) * 0.8)
            self.canvas.create_oval(sx - r, sy - r, sx + r, sy + r, fill=bullet["color"], outline="#FFFFFF")
            self.canvas.create_line(sx - bullet["vx"] * 0.015, sy - bullet["vy"] * 0.006, sx, sy, fill=bullet["color"], width=3)
        for bullet in self.enemy_projectiles:
            sx, sy = self._project(bullet["x"], bullet["y"], 22)
            r = max(3, bullet["r"] * self._depth_scale(bullet["y"]) * 0.9)
            self.canvas.create_oval(sx - r, sy - r, sx + r, sy + r, fill=bullet["color"], outline="#3B102F")

    def _draw_enemies(self):
        for enemy in sorted(self.enemies, key=lambda item: item["y"]):
            r = enemy["r"] * (1 + math.sin(self.game_time * 4 + enemy["phase"]) * 0.04)
            self._draw_iso_disc(enemy["x"], enemy["y"], r, enemy["color"], self._enemy_icon(enemy["kind"]), height=36 if not enemy["boss"] else 62)
            bar_w = max(26, enemy["r"] * 1.8)
            hp_ratio = max(0, enemy["hp"] / enemy["max_hp"])
            sx, sy = self._project(enemy["x"], enemy["y"], 58 if not enemy["boss"] else 88)
            self.canvas.create_rectangle(sx - bar_w / 2, sy - r - 12, sx + bar_w / 2, sy - r - 7, fill="#2B1934", outline="")
            self.canvas.create_rectangle(sx - bar_w / 2, sy - r - 12, sx - bar_w / 2 + bar_w * hp_ratio, sy - r - 7, fill="#FDE68A", outline="")

    def _enemy_icon(self, kind):
        return {
            "runner": "!",
            "spitter": "*",
            "brute": "#",
            "dasher": ">",
            "mender": "+",
            "boss_queen": "Q",
            "boss_mannequin": "M",
            "boss_dragon": "D",
        }.get(kind, "?")

    def _draw_orbs(self):
        count = int(self.stats["orbs"])
        if count <= 0:
            return
        for index in range(count):
            angle = self.game_time * 3.2 + index * math.tau / count
            x = self.player["x"] + math.cos(angle) * 64
            y = self.player["y"] + math.sin(angle) * 64
            sx, sy = self._project(x, y, 44 + math.sin(self.game_time * 5 + index) * 7)
            self.canvas.create_oval(sx - 10, sy - 10, sx + 10, sy + 10, fill="#F9A8D4", outline="#FFFFFF", width=2)

    def _draw_player(self):
        x, y, r = self.player["x"], self.player["y"], self.player["r"]
        flash = self.player["invuln"] > 0 and int(self.game_time * 18) % 2 == 0
        body = "#FFFFFF" if flash else self.colors["app_bg"]
        if self.player["shield"] > 0:
            sx, sy = self._project(x, y, 34)
            self.canvas.create_oval(sx - r - 15, sy - r - 15, sx + r + 15, sy + r + 15, outline="#60A5FA", width=3)
        self._draw_iso_disc(x, y, r, body, "", height=36, outline=self.colors["primary"])
        angle = math.atan2(self.mouse["y"] - y, self.mouse["x"] - x)
        sx, sy = self._project(x, y, 38)
        mx, my = self._project(x + math.cos(angle) * 48, y + math.sin(angle) * 48, 34)
        self.canvas.create_line(sx, sy, mx, my, fill="#F9A8D4", width=4)
        self.canvas.create_oval(sx - 6, sy - 5, sx - 2, sy - 1, fill=self.colors["text"], outline="")
        self.canvas.create_oval(sx + 4, sy - 5, sx + 8, sy - 1, fill=self.colors["text"], outline="")

    def _draw_particles(self):
        for particle in self.particles:
            alpha_size = max(1, particle["size"] * max(0.25, particle["life"]))
            sx, sy = self._project(particle["x"], particle["y"], 26 * max(0.2, particle["life"]))
            self.canvas.create_oval(
                sx - alpha_size,
                sy - alpha_size,
                sx + alpha_size,
                sy + alpha_size,
                fill=particle["color"],
                outline="",
            )

    def _draw_boss_bar(self):
        bosses = [enemy for enemy in self.enemies if enemy.get("boss")]
        if not bosses:
            return
        boss = bosses[0]
        ratio = max(0, boss["hp"] / boss["max_hp"])
        self.canvas.create_rectangle(210, 18, 690, 38, fill="#2B1934", outline="#FFFFFF")
        self.canvas.create_rectangle(212, 20, 212 + 476 * ratio, 36, fill=boss["color"], outline="")
        self.canvas.create_text(450, 48, text=boss["name"], fill="#FFFFFF", font=("Segoe UI Semibold", 11))

    def _draw_weapon_bar(self):
        x = 18
        y = self.height - 44
        for index, key in enumerate(self.weapon_order, start=1):
            data = self.weapon_data[key]
            active = key == self.selected_weapon
            width = 188
            self.canvas.create_rectangle(
                x,
                y,
                x + width,
                y + 30,
                fill=data["color"] if active else "#2B1934",
                outline="#FFFFFF" if active else "#6C366B",
                width=2 if active else 1,
            )
            label = f"{index} {data['name']}"
            self.canvas.create_text(x + 10, y + 15, anchor="w", text=label, fill="#1C1424" if active else "#FFFFFF", font=("Segoe UI Semibold", 9))
            x += width + 10

    def _draw_message(self):
        self.canvas.create_rectangle(24, 18, 196, 68, fill="#FFF7FF", outline=self.colors["primary"], width=2)
        self.canvas.create_text(36, 31, anchor="nw", text=self.message, width=145, fill=self.colors["text"], font=("Segoe UI Semibold", 9))

    def _power_card_rects(self):
        cards = []
        card_w = 250
        gap = 20
        start_x = (self.width - card_w * 3 - gap * 2) / 2
        y1 = 174
        for index in range(3):
            x1 = start_x + index * (card_w + gap)
            cards.append((x1, y1, x1 + card_w, y1 + 172))
        return cards

    def _draw_power_choice(self):
        card_w = 250
        self.canvas.create_rectangle(0, 0, self.width, self.height, fill="#130C1B", stipple="gray50", outline="")
        self.canvas.create_text(
            self.width / 2,
            112,
            text="Escolha um poder roguelike",
            fill="#FFFFFF",
            font=("Segoe UI Semibold", 22),
        )
        for index, (choice, rect) in enumerate(zip(self.power_choices, self._power_card_rects()), start=1):
            x1, y1, x2, y2 = rect
            self.canvas.create_rectangle(x1 + 8, y1 + 10, x2 + 8, y2 + 10, fill="#08050B", outline="")
            self.canvas.create_rectangle(x1, y1, x2, y2, fill="#FFF7FF", outline=self.colors["primary"], width=3)
            self.canvas.create_text(x1 + 18, y1 + 22, anchor="nw", text=f"{index}. {choice['name']}", fill=self.colors["primary"], font=("Segoe UI Semibold", 13), width=card_w - 36)
            self.canvas.create_text(x1 + 18, y1 + 72, anchor="nw", text=choice["desc"], fill=self.colors["text"], font=("Segoe UI", 11), width=card_w - 36)
        self.canvas.create_text(self.width / 2, 386, text="Clique em um card ou pressione Espaco para pegar o primeiro.", fill="#FFFFFF", font=("Segoe UI", 11))

    def _draw_center_panel(self, title, text):
        self.canvas.create_rectangle(170, 186, self.width - 170, 344, fill="#FFF7FF", outline=self.colors["primary"], width=3)
        self.canvas.create_text(self.width / 2, 224, text=title, fill=self.colors["primary"], font=("Segoe UI Semibold", 20))
        self.canvas.create_text(self.width / 2, 272, text=text, fill=self.colors["text"], width=470, font=("Segoe UI", 12))

    def _blend(self, start, end, amount):
        start = start.lstrip("#")
        end = end.lstrip("#")
        values = []
        for index in range(0, 6, 2):
            a = int(start[index:index + 2], 16)
            b = int(end[index:index + 2], 16)
            values.append(int(a + (b - a) * amount))
        return "#" + "".join(f"{value:02x}" for value in values)

    def _on_close(self):
        if self.loop_job is not None:
            self.after_cancel(self.loop_job)
        self.destroy()
