from __future__ import print_function
"""
Multiprocessing job transport for AsyncRE/OpenMM
"""
import os, re, sys, time, shutil, copy, random, signal
import logging

from simtk import openmm as mm
from simtk.openmm.app import *
from simtk.openmm import *
from simtk.unit import *
from datetime import datetime

from atmmetaforce import *
from ommworker import *

class OMMReplica(object):
    #
    # Holds and manages OpenMM state for a replica
    #
    def __init__(self, replica_id, basename, worker, logger):
        self._id = replica_id
        self.basename = basename
        self.worker = worker
        self.context = worker.context
        self.ommsystem = worker.ommsystem
        self.logger = logger
        self.pot = None
        self.par = None
        self.cycle = 1
        self.stateid = None
        self.mdsteps = 0
        self.outfile = None

        state = self.context.getState(getPositions=True, getVelocities=True)
        self.positions = state.getPositions()
        self.velocities = state.getVelocities()

        if not os.path.isdir('r%d' % self._id):
            os.mkdir('r%d' % self._id)
            
        self.open_out()
        #may override stateid, positions, etc.
        self.load_checkpoint()
        self.open_dcd()

    def set_state(self, stateid, par):
        self.stateid = int(stateid)
        self.par = copy.deepcopy(par)
        self.is_state_assigned = True
        self.update_context_from_state()
        
    def get_state(self):
        return (self.stateid, self.par)

    def get_energy(self):
        return self.pot

    def set_energy(self, pot):
        self.pot = copy.deepcopy(pot)
        
    def set_posvel(self, positions, velocities):
        self.positions = copy.deepcopy(positions)
        self.velocities = copy.deepcopy(velocities)

    def open_out(self):
        outfilename =  'r%d/%s.out' % (self._id,self.basename)
        self.outfile = open(outfilename, 'a+')
        if self.outfile is None:
            self.logger.warning("unable to open outfile %s" % outfilename)

    def load_checkpoint(self):
        ckptfile = 'r%d/%s_ckpt.xml' % (self._id,self.basename)
        if os.path.isfile(ckptfile):
            self.logger.info("Loading checkpointfile %s" % ckptfile) 
            self.worker.simulation.loadState(ckptfile)
            self.update_state_from_context()

    def save_checkpoint(self):
        ckptfile = 'r%d/%s_ckpt.xml' % (self._id,self.basename)
        self.update_context_from_state()
        self.worker.simulation.saveState(ckptfile)
        
    def open_dcd(self):
        dcdfilename =  'r%d/%s.dcd' % (self._id,self.basename)
        append = os.path.isfile(dcdfilename)
        if append:
            mode = 'r+b'
        else:
            mode = 'wb'
        self.dcdfile = open(dcdfilename, mode)
        self.dcd = DCDFile(self.dcdfile, self.worker.topology, self.ommsystem.MDstepsize, append=append)
        self.dcdfile.flush() # Force the writing of the DCD header

    def save_dcd(self):
        #TODO
        #boxsize options works only for NVT because the boxsize of the service worker
        #is not updated from the compute worker
        boxsize = self.worker.simulation.context.getState().getPeriodicBoxVectors()
        self.dcd.writeModel(self.positions, periodicBoxVectors=boxsize)

    def set_mdsteps(self, mdsteps):
        self.mdsteps = mdsteps

    def get_mdsteps(self):
        return self.mdsteps

    def set_cycle(self, cycle):
        self.cycle = cycle
        
    def get_cycle(self):
        return self.cycle

    def get_stateid(self):
        return self.stateid


class OMMReplicaTRE(OMMReplica):
    def save_out(self):
        if self.pot is not None and self.par is not None:
            pot_energy = self.pot['potential_energy']
            temperature = self.par['temperature']
            if self.outfile is not None:
                self.outfile.write("%d %f %f\n" % (self.stateid, temperature, pot_energy))

    def update_state_from_context(self):
        self.cycle = int(self.context.getParameter(self.ommsystem.parameter['cycle']))
        self.stateid = int(self.context.getParameter(self.ommsystem.parameter['stateid']))
        self.mdsteps = int(self.context.getParameter(self.ommsystem.parameter['mdsteps']))
        if self.par is None:
            self.par = {}
        self.par['temperature'] = self.context.getParameter(self.ommsystem.parameter['temperature'])*kelvin
        if self.pot is None:
            self.pot = {}
        self.pot['potential_energy'] = self.context.getParameter(self.ommsystem.parameter['potential_energy'])*kilojoules_per_mole
        state = self.context.getState(getPositions=True, getVelocities=True)
        self.positions = state.getPositions()
        self.velocities = state.getVelocities()

    def update_context_from_state(self):
        self.context.setParameter(self.ommsystem.parameter['cycle'], self.cycle)
        self.context.setParameter(self.ommsystem.parameter['stateid'], self.stateid)
        self.context.setParameter(self.ommsystem.parameter['mdsteps'], self.mdsteps)
        if self.par is None:
            self.context.setParameter(self.ommsystem.parameter['temperature'], self.par['temperature']/kelvin)
        if self.pot is None:
            self.context.setParameter(self.ommsystem.parameter['potential_energy'], self.pot['potential_energy']/kilojoules_per_mole)

class OMMReplicaATM(OMMReplica):
    def save_out(self):
        if self.pot is not None and self.par is not None:
            pot_energy = self.pot['potential_energy']
            pert_energy = self.pot['perturbation_energy']
            bias_energy = self.pot['bias_energy']
            temperature = self.par['temperature']
            lmbd1 = self.par['lambda1']
            lmbd2 = self.par['lambda2']
            alpha = self.par['alpha']
            u0 = self.par['u0']
            w0 = self.par['w0']
            direction = self.par['atmdirection']
            if self.outfile is not None:
                self.outfile.write("%d %f %f %f %f %f %f %f %f %f %f\n" % (self.stateid, temperature/kelvin, direction, lmbd1, lmbd2, alpha*kilocalories_per_mole, u0/kilocalories_per_mole, w0/kilocalories_per_mole, pot_energy/kilocalories_per_mole, pert_energy/kilocalories_per_mole, bias_energy/kilocalories_per_mole))
                self.outfile.flush()
            else:
                self.logger.warning("unable to save output")
        else:
            self.logger.warning("unable to save output")

    def update_state_from_context(self):
        self.cycle = int(self.context.getParameter(self.ommsystem.parameter['cycle']))
        self.stateid = int(self.context.getParameter(self.ommsystem.parameter['stateid']))
        self.mdsteps = int(self.context.getParameter(self.ommsystem.parameter['mdsteps']))
        if self.par is None:
            self.par = {}
        self.par['temperature'] = self.context.getParameter(self.ommsystem.parameter['temperature'])*kelvin
        self.par['lambda1'] = self.context.getParameter(self.ommsystem.atmforce.Lambda1())
        self.par['lambda2'] = self.context.getParameter(self.ommsystem.atmforce.Lambda2())
        self.par['alpha'] = self.context.getParameter(self.ommsystem.atmforce.Alpha())/kilojoules_per_mole
        self.par['u0'] = self.context.getParameter(self.ommsystem.atmforce.U0())*kilojoules_per_mole
        self.par['w0'] = self.context.getParameter(self.ommsystem.atmforce.W0())*kilojoules_per_mole
        self.par['atmdirection'] = self.context.getParameter(self.ommsystem.atmforce.Direction())
        self.par['atmintermediate'] = self.context.getParameter(self.ommsystem.parameter['atmintermediate'])
        if self.pot is None:
            self.pot = {}
        self.pot['potential_energy'] = self.context.getParameter(self.ommsystem.parameter['potential_energy'])*kilojoules_per_mole
        self.pot['perturbation_energy'] = self.context.getParameter(self.ommsystem.parameter['perturbation_energy'])*kilojoules_per_mole
        self.pot['bias_energy'] = self.context.getParameter(self.ommsystem.parameter['bias_energy'])*kilojoules_per_mole
        state = self.context.getState(getPositions=True, getVelocities=True)
        self.positions = state.getPositions()
        self.velocities = state.getVelocities()

    def update_context_from_state(self):
        self.context.setParameter(self.ommsystem.parameter['cycle'], self.cycle)
        self.context.setParameter(self.ommsystem.parameter['stateid'], self.stateid)
        self.context.setParameter(self.ommsystem.parameter['mdsteps'], self.mdsteps)
        if self.par is not None:
            self.context.setParameter(self.ommsystem.parameter['temperature'], self.par['temperature']/kelvin)
            self.context.setParameter(self.ommsystem.atmforce.Lambda1(), self.par['lambda1'])
            self.context.setParameter(self.ommsystem.atmforce.Lambda2(), self.par['lambda2'])
            self.context.setParameter(self.ommsystem.atmforce.Alpha(), self.par['alpha']*kilojoules_per_mole)
            self.context.setParameter(self.ommsystem.atmforce.U0(), self.par['u0']/kilojoules_per_mole)
            self.context.setParameter(self.ommsystem.atmforce.W0(), self.par['w0']/kilojoules_per_mole)
            self.context.setParameter(self.ommsystem.atmforce.Direction(), self.par['atmdirection'])
            self.context.setParameter(self.ommsystem.parameter['atmintermediate'], self.par['atmintermediate'])
        if self.pot is not None:
            self.context.setParameter(self.ommsystem.parameter['potential_energy'], self.pot['potential_energy']/kilojoules_per_mole)
            self.context.setParameter(self.ommsystem.parameter['perturbation_energy'], self.pot['perturbation_energy']/kilojoules_per_mole)
            self.context.setParameter(self.ommsystem.parameter['bias_energy'], self.pot['bias_energy']/kilojoules_per_mole)
        self.context.setPositions(self.positions)
        self.context.setVelocities(self.velocities)
