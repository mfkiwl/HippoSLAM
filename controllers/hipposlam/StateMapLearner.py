"""tabular_qlearning controller."""

# You may need to import some classes of the controller module. Ex:
from controller import Robot, Motor
from controller import Supervisor
import gym
import numpy as np
# from stable_baselines3.common.env_checker import check_env
from stable_baselines3 import PPO
from controllers.hipposlam.hipposlam.sequences import Sequences, HippoLearner

import os
os.environ['KMP_DUPLICATE_LIB_OK']='True'

class SimpleQ(Supervisor, gym.Env):
    def __init__(self, max_episode_steps=1000):
        super().__init__()

        # Open AI Gym generic
        # lowBox = np.array([-7, -3, -1, -1, -1, -2 * np.pi], dtype=np.float64)
        # highBox = np.array([7, 5, 1, 1, 1, 2 * np.pi], dtype=np.float64)

        # self.observation_space = gym.spaces.Box(0, 1000, dtype=np.int)
        self.observation_space = gym.spaces.Discrete(1000)
        # self.obs_dim = 200
        # self.observation_space = gym.spaces.Box(0, 100, shape=(self.obs_dim,))

        self.state = None
        self.spec = gym.envs.registration.EnvSpec(id='WeBotsQ-v0', max_episode_steps=max_episode_steps)

        # Supervisor
        self.supervis = self.getSelf()
        self.translation_field = self.supervis.getField('translation')
        self.rotation_field = self.supervis.getField('rotation')
        self.fallen = False
        self.fallen_seq = 0

        # Self position
        self.x, self.y = None, None
        self.stuck_m = 0
        self.stuck_epsilon = 0.0001
        self.stuck_thresh = 8

        # Environment specific
        self.__timestep = int(self.getBasicTimeStep())  # default 32ms
        self.thetastep = self.__timestep * 32  # 32 * 32 = 1024 ms

        # Wheels
        self.leftMotor1 = self.getDevice('wheel1')
        self.leftMotor2 = self.getDevice('wheel3')
        self.rightMotor1 = self.getDevice('wheel2')
        self.rightMotor2 = self.getDevice('wheel4')
        self.MAX_SPEED = 15

        # Action - 'Forward', 'back', 'left', 'right'
        self.action_space = gym.spaces.Discrete(3)
        self.turn_steps = self.thetastep
        self.forward_steps = self.thetastep
        self.move_d = self.MAX_SPEED * 2 / 3
        self._action_to_direction = {
            0: np.array([self.move_d, self.move_d]),  # Forward
            1: np.array([-self.move_d, self.move_d]) * 0.5,  # Left turn
            2: np.array([self.move_d, -self.move_d]) * 0.5,  # Right turn
        }

        # Camera
        self.camera_timestep = self.thetastep
        self.cam = self.getDevice('camera')
        self.cam.enable(self.camera_timestep)
        self.cam.recognitionEnable(self.camera_timestep)
        self.cam_width = self.cam.getWidth()
        self.cam_height = self.cam.getHeight()

        # hippoSlam
        self.fpos_dict = dict()
        self.obj_dist = 2  # in meters
        R, L = 5, 10
        self.seq = Sequences(R=R, L=L, reobserve=False)
        self.HL = HippoLearner(R, L, L)

        # Tools
        self.keyboard = self.getKeyboard()
        self.keyboard.enable(self.__timestep)

        # Data I/O
        self.io_pth = "data/statemaps2.txt"
        with open(self.io_pth, mode='w') as f:
            f.write('t,sid,x,y,rotz,rota,done,reward\n')

    def wait_keyboard(self):
        while self.keyboard.getKey() != ord('Y'):
            super().step(self.__timestep)

    def get_obs(self):
        id_list = self.recognize_objects()
        self.seq.step(id_list)
        self.HL.step(self.seq.X)
        sid, Snodes = self.HL.infer_state(self.seq.X)
        # vec = np.zeros(self.obs_dim)
        # vec[:self.HL.N] = Snodes
        return sid

    def reset(self):
        # Reset the simulation
        self.simulationResetPhysics()
        self.simulationReset()
        super().step(self.__timestep)

        self.translation_field = self.supervis.getField('translation')
        self.rotation_field = self.supervis.getField('rotation')

        # Reset position and velocity
        x = np.random.uniform(3.45, 6.3, size=1)
        y = np.random.uniform(1.35, 3.85, size=1)
        a = np.random.uniform(-np.pi, np.pi, size=1)
        self.stuck_m = 0
        self._set_translation(x, y, 0.07)  # 4.18, 2.82, 0.07
        self._set_rotation(0, 0, -1, a)  # 0, 0, -1, 1.57
        x, y, _ = self._get_translation()
        self.x, self.y = x, y
        for motor in [self.leftMotor1, self.leftMotor2, self.rightMotor1, self.rightMotor2]:
            motor.setVelocity(0)
            motor.setPosition(float('inf'))
        # Reset hipposlam
        self.seq.reset_activity()

        # Infer the first step
        sid = self.get_obs()

        # IO
        with open(self.io_pth, 'a') as f:
            f.write('%0.4f,%d,%0.3f,%0.3f,%0.3f,%0.3f,%d,%d\n' % (self.getTime(), sid, x, y, -1, a, 0, 0))

        # Internals
        super().step(self.__timestep)


        return sid

    def step(self, action):

        leftd, rightd = self._action_to_direction[action]
        self.leftMotor1.setVelocity(leftd)
        self.leftMotor2.setVelocity(leftd)
        self.rightMotor1.setVelocity(rightd)
        self.rightMotor2.setVelocity(rightd)
        super().step(self.thetastep)


        sid = self.get_obs()


        new_x, new_y, _ = self._get_translation()
        rotx, roty, rotz, rota = self._get_rotation()
        fallen = (np.abs(rotx) > 0.5) | (np.abs(roty) > 0.5)
        dpos = np.sqrt((new_x-self.x)**2 + (new_y - self.y)**2)
        stuck_count = dpos < self.stuck_epsilon
        self.stuck_m = 0.9 * self.stuck_m + stuck_count * 1.0
        stuck = self.stuck_m > self.stuck_thresh
        print('\rInterred state = %d/%d, dpos = %0.8f, %s, stuck_m = %0.4f' % (sid, self.HL.N, dpos, str(stuck_count), self.stuck_m),
              end='', flush=True)
        self.x, self.y = new_x, new_y


        # Done
        done = bool((new_x < 2) or (new_y < 0))

        # Reward
        reward = 1 if done else 0
        if done:
            print('\n================== Robot has reached the goal =================================\n')

        if fallen:
            print('\n================== Robot has fallen %s=============================\n'%(str(fallen)))
            print('Rotations = %0.4f, %0.4f, %0.4f, %0.4f '%(rotx, roty, rotz, rota))
            print('Abs x and y = %0.4f, %0.4f'%(np.abs(rotx), (np.abs(roty))))
            reward, done = -1, True
            if self.fallen:
                self.fallen_seq += 1
            if self.fallen_seq > 5:
                breakpoint()

        if stuck:
            print("\n================== Robot is stuck =================================\n")
            reward, done = -1, True

        self.fallen = fallen

        with open(self.io_pth, 'a') as f:
            f.write('%0.4f,%d,%0.3f,%0.3f,%0.3f,%0.3f,%d,%d\n'%(self.getTime(), sid, new_x, new_y, rotz, rota, int(done), reward))


        return sid, reward, done, {}

    def recognize_objects(self):
        objs = self.cam.getRecognitionObjects()
        idlist = [obj.getId() for obj in objs]

        # Distance from robot to the objects
        x, y, z = self._get_translation()
        closeIDlist = []
        farIDlist = []
        closestID = None
        closestdist = 100
        for objid in idlist:

            # Obtain object position
            obj_node = self.getFromId(objid)
            objpos = obj_node.getPosition()

            # Store object positions
            if str(objid) not in self.fpos_dict:
                fpos_key = '%d'%objid
                self.fpos_dict[fpos_key] = objpos
                # print('Insert Id=%s with position ' % (fpos_key), objpos)

            # Compute distance
            dist = np.sqrt((x - objpos[0]) ** 2 + (y - objpos[1])**2)
            if dist < closestdist:
                closestdist = dist
                closestID = objid
            if dist < self.obj_dist:
                # print('Close object %d added'%(objid))
                closeIDlist.append('%d'%(objid))

            else:
                # print('Distant object %d added' % (objid))
                farIDlist.append('%d'%objid)

        close_to_dist_list = []
        if (len(closeIDlist) == 0) and (closestID is not None):
            closeIDlist.append('%d'%(closestID))
        for c in closeIDlist:
            for d in farIDlist:
                cd = c + "_" + d
                close_to_dist_list.append(cd)
        return close_to_dist_list



    def _get_translation(self):
        return self.translation_field.getSFVec3f()
    def _get_rotation(self):
        return self.rotation_field.getSFRotation()
    def _set_translation(self, x, y, z):
        self.translation_field.setSFVec3f([x, y, z])
        return None

    def _set_rotation(self, rotx, roty, rotz, rot):
        self.rotation_field.setSFRotation([rotx, roty, rotz, rot])
        return None


def main():
    # Initialize the environment

    env = SimpleQ()
    env.reset()
    # for i in range(18):
    #     env.step(0)
    # for i in range(3):
    #     env.step(2)
    # for i in range(10):
    #     env.step(0)
    #

    # Train
    model = PPO('MlpPolicy', env, verbose=1, learning_rate=0.01)
    model.learn(total_timesteps=2e5)
    #
    # Replay
    print('Training is finished, press `Y` for replay...')
    env.wait_keyboard()
    #
    obs = env.reset()
    for _ in range(100000):
        action, _states = model.predict(obs)
        obs, reward, done, info = env.step(action)
        print(obs, reward, done, info)
        if done:
            obs = env.reset()

    # print(env.get_obs())
    # steps = [0] * 6 + [1] * 4
    # for ai in steps:
    #     new_pos, reward, done, _ = env.step(ai)
    #     print('State ori:', np.around(new_pos, 4),
    #           '\nStateReal: ', np.around(env.get_obs(), 4),
    #           '\nReward: ', reward,
    #           '\nDone: ', done)


if __name__ == '__main__':
    main()
