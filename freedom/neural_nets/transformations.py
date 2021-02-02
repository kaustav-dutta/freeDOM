"""Transformation tensorflow layers"""
import tensorflow as tf
import numpy as np
from scipy import constants


class hitnet_trafo(tf.keras.layers.Layer):
    '''Class to transfor inputs for Hit Net
    Independent angles
    '''
    speed_of_light = constants.c * 1e-9 # c in m / ns

    
    def __init__(self, labels, min_energy=0.1, max_energy=1e4, use_pmt_dir=False):
        '''
        Parameters:
        -----------

        labels : list
            list of labels corresponding to the data array
        '''
        
        super().__init__()

        self.labels = labels
        self.min_energy = min_energy
        self.max_energy = max_energy
        self.use_pmt_dir = use_pmt_dir
        
        self.azimuth_idx = labels.index('azimuth')
        self.zenith_idx = labels.index('zenith')
        self.time_idx = labels.index('time')
        self.x_idx = labels.index('x')
        self.y_idx = labels.index('y')
        self.z_idx = labels.index('z')
        self.cascade_energy_idx = labels.index('cascade_energy')
        self.track_energy_idx = labels.index('track_energy')
        
        
        #self.e_cscd_bias = self.add_weight(shape=(1,), initializer="zeros", trainable=True)
        #self.e_cscd_scale = self.add_weight(shape=(1,), initializer="zeros", trainable=True)
        #loc_x_bias
        

    def get_config(self):
        return {'labels': self.labels, 'max_energy': self.min_energy, 'max_energy': self.max_energy, 
                'use_pmt_dir': self.use_pmt_dir}


    def TimeResidual(self, hits, params, Index=1.33):
        hitpos = hits[:,:3]
        T_exp = self.CherenkovTime(params, hitpos, Index)
        T_meas = hits[:,3] - params[:, self.time_idx]
        return T_meas - T_exp

    def CherenkovTime(self, params, position, Index=1.33):
        changle = np.arccos(1/Index).astype(np.float32) #tf.math.acos(1/Index)
        length = tf.clip_by_value(params[:, self.track_energy_idx], self.min_energy, self.max_energy) * 5 #m
        Length = tf.stack([length, length, length], axis=1)

        # closest point on (inf) track, dist, dist along track and direction
        appos, apdist, s, Dir = self.ClosestApproachCalc(params, position)
        a = s - apdist/tf.math.tan(changle)

        return tf.where(a <= 0.,
                        tf.norm(position-params[:,:3], axis=1) * Index/self.speed_of_light,
                        tf.where(a <= length,
                                 (a + apdist/np.sin(changle)*Index) / self.speed_of_light,
                                 (length + tf.norm(position-(params[:,:3] + Length*Dir), axis=1)*Index) / self.speed_of_light
                                )
                       )

    def ClosestApproachCalc(self, params, position):
        theta  = params[:, self.zenith_idx]
        phi    = params[:, self.azimuth_idx]
        pos0_x = params[:, self.x_idx]
        pos0_y = params[:, self.y_idx]
        pos0_z = params[:, self.z_idx]

        e_x = -tf.math.sin(theta)*tf.math.cos(phi)
        e_y = -tf.math.sin(theta)*tf.math.sin(phi)
        e_z = -tf.math.cos(theta)

        h_x = position[:,0] - pos0_x
        h_y = position[:,1] - pos0_y
        h_z = position[:,2] - pos0_z

        s = e_x*h_x + e_y*h_y + e_z*h_z

        pos2_x = pos0_x + s*e_x
        pos2_y = pos0_y + s*e_y
        pos2_z = pos0_z + s*e_z

        appos = tf.stack([pos2_x, pos2_y, pos2_z], axis=1)
        apdist = tf.norm(position-appos, axis=1)
        Dir = tf.stack([e_x, e_y, e_z], axis=1)

        return appos, apdist, s, Dir


    def call(self, hit, params):
        '''
        Parameters:
        -----------

        hit : tensor
            shape (N, 4+), containing hit DOM position x, y, z and hit time

        params : tensor
            shape (N, len(labels))

        '''
        
        cosphi = tf.math.cos(params[:, self.azimuth_idx])
        sinphi = tf.math.sin(params[:, self.azimuth_idx])
        
        sintheta = tf.math.sin(params[:, self.zenith_idx])
        dir_x = sintheta * cosphi
        dir_y = sintheta * sinphi
        dir_z = tf.math.cos(params[:, self.zenith_idx])
        
        dx = params[:, self.x_idx] - hit[:,0]
        dy = params[:, self.y_idx] - hit[:,1]
        dz = params[:, self.z_idx] - hit[:,2]
        
        # distance DOM - vertex
        rho = tf.math.sqrt(tf.math.square(dx) + tf.math.square(dy))
        dist = tf.math.sqrt(tf.math.square(dx) + tf.math.square(dy) + tf.math.square(dz))     
        
        absdeltaphidir = tf.abs(tf.math.acos(
                                tf.clip_by_value(-tf.math.divide_no_nan((cosphi*dx + sinphi*dy), rho),
                                                 clip_value_min = -1.,
                                                 clip_value_max = +1.,
                                                )
                                            )
                                )

        costhetadir = tf.math.divide_no_nan(rho, dist)
        sinthetadir = tf.sqrt(1 - tf.clip_by_value(tf.math.square(costhetadir), 0, 1)) # can produce NaN on CPU without clip
        # so it is 0 at the poles?
        absdeltaphidir *= sintheta * sinthetadir
        
        #dt = hit[:,3] - params[:, self.time_idx]
        ## difference c*t - r
        #delta = dt * self.speed_of_light - dist
        delta = self.TimeResidual(hit, params)

        cascade_energy = tf.math.log(tf.clip_by_value(params[:, self.cascade_energy_idx], self.min_energy, self.max_energy))
        track_energy = tf.math.log(tf.clip_by_value(params[:, self.track_energy_idx], self.min_energy, self.max_energy))
        
        if self.use_pmt_dir:
            pmt_x = tf.math.sin(hit[:,7]) * tf.math.cos(hit[:,8])
            pmt_y = tf.math.sin(hit[:,7]) * tf.math.sin(hit[:,8])
            pmt_z = tf.math.cos(hit[:,7])
            
            cos_pmtd = (pmt_x*dx + pmt_y*dy + pmt_z*dz)/(dist) # pmt looks to event?
            cos_dird = (dir_x*dx + dir_y*dy + dir_z*dz)/(dist) # event flies to pmt?
            
            out = [delta, dist, costhetadir, absdeltaphidir, dir_x, dir_y, dir_z, dx, dy, dz, hit[:,0], hit[:,1], hit[:,2],
                   hit[:,5], hit[:,6], cos_pmtd, cos_dird, cascade_energy, track_energy] #hit[:,7], hit[:,8]
        else:
            out = [delta, dist, costhetadir, absdeltaphidir, dir_x, dir_y, dir_z, dx, dy, dz, hit[:,0], hit[:,1], hit[:,2],
                   hit[:,5], hit[:,6], cascade_energy, track_energy]
        out = tf.stack(out, axis=1)

        return out
    
class domnet_trafo(tf.keras.layers.Layer):
    '''Class to transfor inputs for domnet
    '''
    
    def __init__(self, labels, min_energy=0.1, max_energy=1e4):
        '''
        Parameters:
        -----------

        labels : list
            list of labels corresponding to the data array
        '''
        
        super().__init__()

        self.labels = labels
        self.min_energy = min_energy
        self.max_energy = max_energy
        
        self.azimuth_idx = labels.index('azimuth')
        self.zenith_idx = labels.index('zenith')
        self.x_idx = labels.index('x')
        self.y_idx = labels.index('y')
        self.z_idx = labels.index('z')
        self.cascade_energy_idx = labels.index('cascade_energy')
        self.track_energy_idx = labels.index('track_energy')
        
    def get_config(self):
        return {'labels': self.labels, 'max_energy': self.min_energy, 'max_energy': self.max_energy}

    def call(self, dom, params):
        '''
        Parameters:
        -----------

        dom : tensor
            shape (N, 4), containing hit dom position x, y, z, and charge

        params : tensor
            shape (N, len(labels))

        '''
        
        cosphi = tf.math.cos(params[:, self.azimuth_idx])
        sinphi = tf.math.sin(params[:, self.azimuth_idx])
        sintheta = tf.math.sin(params[:, self.zenith_idx])
        
        dir_x = sintheta * cosphi
        dir_y = sintheta * sinphi
        dir_z = tf.math.cos(params[:, self.zenith_idx])
        
        dx = params[:, self.x_idx] - dom[:,0]
        dy = params[:, self.y_idx] - dom[:,1]
        dz = params[:, self.z_idx] - dom[:,2]
        
        # distance DOM - vertex
        rho = tf.math.sqrt(tf.math.square(dx) + tf.math.square(dy))
        dist = tf.math.sqrt(tf.math.square(dx) + tf.math.square(dy) + tf.math.square(dz))
        
        absdeltaphidir = tf.abs(tf.math.acos(
                                tf.clip_by_value(-tf.math.divide_no_nan((cosphi*dx + sinphi*dy), rho),
                                                 clip_value_min = -1.,
                                                 clip_value_max = +1.,
                                                )
                                            )
                                )

        costhetadir = tf.math.divide_no_nan(rho, dist)
        sinthetadir = tf.sqrt(1 - tf.clip_by_value(tf.math.square(costhetadir), 0, 1)) # can produce NaN on CPU without clip
        
        # so it is 0 at the poles?
        absdeltaphidir *= sintheta * sinthetadir

        cascade_energy = tf.math.log(tf.clip_by_value(params[:, self.cascade_energy_idx], self.min_energy, self.max_energy))
        track_energy = tf.math.log(tf.clip_by_value(params[:, self.track_energy_idx], self.min_energy, self.max_energy))

        out = tf.stack([
                 dom[:,0],
                 dom[:,1],
                 dom[:,2],
                 dom[:,3],
                 dist,
                 costhetadir,
                 absdeltaphidir,
                 dir_x,
                 dir_y,
                 dir_z,
                 dx,
                 dy,
                 dz,
                 cascade_energy,
                 track_energy
                ],
                axis=1
                )    
            
        return out
    
class stringnet_trafo(tf.keras.layers.Layer):
    '''Class to transfor inputs for stringnet
    '''
    
    def __init__(self, labels, min_energy=0.1, max_energy=1e4):
        '''
        Parameters:
        -----------

        labels : list
            list of labels corresponding to the data array
        '''
        
        super().__init__()

        self.labels = labels
        self.min_energy = min_energy
        self.max_energy = max_energy
        
        self.azimuth_idx = labels.index('azimuth')
        self.zenith_idx = labels.index('zenith')
        self.x_idx = labels.index('x')
        self.y_idx = labels.index('y')
        self.z_idx = labels.index('z')
        self.cascade_energy_idx = labels.index('cascade_energy')
        self.track_energy_idx = labels.index('track_energy')
        
    def get_config(self):
        return {'labels': self.labels, 'max_energy': self.min_energy, 'max_energy': self.max_energy}

    def call(self, string, params):
        '''
        Parameters:
        -----------

        string : tensor
            shape (N, 5), containing hit string position x, y, min(z), charge, and nChannels

        params : tensor
            shape (N, len(labels))

        '''
        
        dir_x = tf.math.sin(params[:, self.zenith_idx]) * tf.math.cos(params[:, self.azimuth_idx])
        dir_y = tf.math.sin(params[:, self.zenith_idx]) * tf.math.sin(params[:, self.azimuth_idx])
        dir_z = tf.math.cos(params[:, self.zenith_idx])
        
        dx = params[:, self.x_idx] - string[:,0]
        dy = params[:, self.y_idx] - string[:,1]
        dz = params[:, self.z_idx] - string[:,2]
        
        # distance string - vertex
        rho = tf.math.sqrt(tf.math.square(dx) + tf.math.square(dy))

        cascade_energy = tf.math.log(tf.clip_by_value(params[:, self.cascade_energy_idx], self.min_energy, self.max_energy))
        track_energy = tf.math.log(tf.clip_by_value(params[:, self.track_energy_idx], self.min_energy, self.max_energy))
        
        out = tf.stack([
                 string[:,0],
                 string[:,1],
                 string[:,2],
                 string[:,3],
                 string[:,4],
                 rho,
                 dir_x,
                 dir_y,
                 dir_z,
                 dx,
                 dy,
                 dz,
                 cascade_energy,
                 track_energy
                ],
                axis=1
                )    
            
        return out
    
class layernet_trafo(tf.keras.layers.Layer):
    '''Class to transfor inputs for layernet
    '''
    
    def __init__(self, labels, min_energy=0.1, max_energy=1e4):
        '''
        Parameters:
        -----------

        labels : list
            list of labels corresponding to the data array
        '''
        
        super().__init__()

        self.labels = labels
        self.min_energy = min_energy
        self.max_energy = max_energy
        
        self.azimuth_idx = labels.index('azimuth')
        self.zenith_idx = labels.index('zenith')
        self.x_idx = labels.index('x')
        self.y_idx = labels.index('y')
        self.z_idx = labels.index('z')
        self.cascade_energy_idx = labels.index('cascade_energy')
        self.track_energy_idx = labels.index('track_energy')
        
    def get_config(self):
        return {'labels': self.labels, 'max_energy': self.min_energy, 'max_energy': self.max_energy}

    def call(self, layer, params):
        '''
        Parameters:
        -----------

        layer : tensor
            shape (N, 4), containing hit layer nDOMs, z position, charge, and nChannels

        params : tensor
            shape (N, len(labels))

        '''
        
        dir_x = tf.math.sin(params[:, self.zenith_idx]) * tf.math.cos(params[:, self.azimuth_idx])
        dir_y = tf.math.sin(params[:, self.zenith_idx]) * tf.math.sin(params[:, self.azimuth_idx])
        dir_z = tf.math.cos(params[:, self.zenith_idx])
        
        dz = params[:, self.z_idx] - layer[:,1]
        
        cascade_energy = tf.math.log(tf.clip_by_value(params[:, self.cascade_energy_idx], self.min_energy, self.max_energy))
        track_energy = tf.math.log(tf.clip_by_value(params[:, self.track_energy_idx], self.min_energy, self.max_energy))
        
        out = tf.stack([
                 layer[:,0],
                 layer[:,1],
                 layer[:,2],
                 layer[:,3],
                 dir_x,
                 dir_y,
                 dir_z,
                 params[:, self.x_idx],
                 params[:, self.y_idx],
                 dz,
                 cascade_energy,
                 track_energy
                ],
                axis=1
                )    
            
        return out
    
class chargenet_trafo(tf.keras.layers.Layer):
    '''Class to transfor inputs for Charget Net
    '''
    
    def __init__(self, labels, min_energy=0.1, max_energy=1e4):
        '''
        Parameters:
        -----------

        labels : list
            list of labels corresponding to the data array
        '''
        
        super().__init__()
        
        self.labels = labels
        self.min_energy = min_energy
        self.max_energy = max_energy
                
        self.azimuth_idx = labels.index('azimuth')
        self.zenith_idx = labels.index('zenith')
        self.x_idx = labels.index('x')
        self.y_idx = labels.index('y')
        self.z_idx = labels.index('z')
        self.cascade_energy_idx = labels.index('cascade_energy')
        self.track_energy_idx = labels.index('track_energy')
        
    def get_config(self):
        return {'labels': self.labels, 'max_energy': self.min_energy, 'max_energy': self.max_energy}
    
    def call(self, charge, params):
        '''
        Parameters:
        -----------

        charge : tensor
            shape (N, 2), containing the event total charge and number of hit DOMs

        params : tensor
            shape (N, len(labels))

        '''
        
        dir_x = tf.math.sin(params[:, self.zenith_idx]) * tf.math.cos(params[:, self.azimuth_idx])
        dir_y = tf.math.sin(params[:, self.zenith_idx]) * tf.math.sin(params[:, self.azimuth_idx])
        dir_z = tf.math.cos(params[:, self.zenith_idx])
        

        cascade_energy = tf.math.log(tf.clip_by_value(params[:, self.cascade_energy_idx], self.min_energy, self.max_energy))
        track_energy = tf.math.log(tf.clip_by_value(params[:, self.track_energy_idx], self.min_energy, self.max_energy))
        
        if charge.shape[1] == 2:
            out = tf.stack([
                     charge[:,0],
                     charge[:,1], #n_channels
                     params[:, self.x_idx],
                     params[:, self.y_idx],
                     params[:, self.z_idx],
                     dir_x,
                     dir_y,
                     dir_z,
                     cascade_energy,
                     track_energy,
                    ],
                    axis=1
                    )
        elif charge.shape[1] == 6:
            out = tf.stack([
                     charge[:,0],
                     charge[:,1], #n_channels
                     charge[:,2],
                     charge[:,3], #n_channels
                     charge[:,4],
                     charge[:,5], #n_channels
                     params[:, self.x_idx],
                     params[:, self.y_idx],
                     params[:, self.z_idx],
                     dir_x,
                     dir_y,
                     dir_z,
                     cascade_energy,
                     track_energy,
                    ],
                    axis=1
                    )

        return out


def test_hitnet_trafo():
    t = hitnet_trafo(labels = ['x', 'y', 'z', 'time', 'azimuth','zenith', 'cascade_energy', 'track_energy'])
    t(np.zeros((1, 9), dtype=np.float32), np.ones((1, 8), dtype=np.float32))
