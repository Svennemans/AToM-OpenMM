
from openmmml.mlpotential import MLPotential, MLPotentialImpl, MLPotentialImplFactory
import openmmtorch
import torch as pt
from torchmdnet.models.model import load_model


class TorchMDNETForce(pt.nn.Module):

    def __init__(self, model_file, atomic_numbers, atom_indices, max_num_neighbors):
        super().__init__()

        self.model = load_model(model_file, derivative=False, max_num_neighbors=max_num_neighbors)
        for parameter in self.model.parameters():
            parameter.requires_grad = False

        self.register_buffer("atom_indices", pt.tensor(atom_indices, dtype=pt.long))
        self.register_buffer("atomic_numbers", pt.tensor(atomic_numbers, dtype=pt.long)[self.atom_indices])

    def forward(self, positions):
        positions = pt.index_select(positions, 0, self.atom_indices).to(pt.float32) * 10 # nm --> A
        return self.model(self.atomic_numbers, positions)[0] * 96.4915666370759 # eV -> kJ/mol


class TorchMDNETImpl(MLPotentialImpl):

    def __init__(self, name, model_file, max_num_neighbors, use_cuda_graphs):
        self.name = name
        self.model_file = model_file
        self.max_num_neighbors = int(max_num_neighbors)
        self.use_cuda_graphs = bool(use_cuda_graphs)

    def addForces(self, topology, system, atom_indices, force_group):
        atomic_numbers = [atom.element.atomic_number for atom in topology.atoms()]

        force = TorchMDNETForce(self.model_file, atomic_numbers, atom_indices, self.max_num_neighbors)
        force = openmmtorch.TorchForce(pt.jit.script(force))
        force.setProperty("useCUDAGraphs", "true" if self.use_cuda_graphs else "false")
        force.setForceGroup(force_group)
        system.addForce(force)


class TorchMDNETImplFactory(MLPotentialImplFactory):

    def createImpl(self, name, model_file, max_num_neighbors, use_cuda_graphs=True):
        return TorchMDNETImpl(name, model_file, max_num_neighbors, use_cuda_graphs)


MLPotential.registerImplFactory('TorchMD-NET', TorchMDNETImplFactory())
