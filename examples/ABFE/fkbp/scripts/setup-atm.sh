#!/bin/bash

#load settings
work_dir=$(pwd)
scripts_dir=${work_dir}/scripts
source ${scripts_dir}/setup-settings.sh

cd ${work_dir} || exit 1

#process each ligand
nligands=${#ligands[@]}
nlig_m1=$(expr ${#ligands[@]} - 1)

cd ${work_dir} || exit 1
for l in `seq 0 ${nlig_m1}` ; do

    lig=${ligands[$l]}

    echo "Processing ligand  $lig..."


    #assign GAFF2 parameters to ligands
    cd ${work_dir}/ligands || exit 1
    
    if [ ! -f ${lig}-p.mol2 ] ; then
	charge=$( awk 'BEGIN{charge = 0} ; NF == 9 {charge += $9} ; END {if (charge >= 0) {print int(charge + 0.5)} else {print int(charge -0.5)}}' < ${lig}.mol2 ) || exit 1
	echo "charge: $charge"
	echo "antechamber -pl 15 -fi mol2 -fo mol2 -i ${lig}.mol2 -o ${lig}-p.mol2 -c bcc -nc ${charge} -at gaff2"
	antechamber -pl 15 -fi mol2 -fo mol2 -i ${lig}.mol2 -o ${lig}-p.mol2 -c bcc -nc ${charge} -at gaff2 || exit 1
	echo "parmchk2 -i ${lig}-p.mol2 -o ${lig}-p.frcmod -f mol2"
	parmchk2 -i ${lig}-p.mol2 -o ${lig}-p.frcmod -f mol2 || exit 1
    else
	echo "Parameters already generated for ${lig}"
    fi

    #creates system in complexes folder
    jobname=${receptor}-${lig}
    mkdir -p ${work_dir}/complexes/${jobname} || exit 1
    
    cd ${work_dir}/complexes/${jobname}    || exit 1

    rcptpdb=${work_dir}/receptor/${receptor}.pdb
    ligmol2=${work_dir}/ligands/${lig}-p.mol2
    ligfrcmod=${work_dir}/ligands/${lig}-p.frcmod
    
    cat >tleap.cmd <<EOF
source leaprc.protein.ff14SB
source leaprc.gaff2
source leaprc.water.tip3p
RCPT = loadpdb "${work_dir}/receptor/${receptor}.pdb"
LIG = loadmol2 "${work_dir}/ligands/${lig}-p.mol2"
loadamberparams "${work_dir}/ligands/${lig}-p.frcmod"
MOL = combine {RCPT LIG}
addions2 MOL Na+ 0
addions2 MOL Cl- 0
solvateBox MOL TIP3PBOX 10.0
saveamberparm MOL ${jobname}.prmtop ${jobname}.inpcrd
savepdb MOL ${jobname}.pdb
quit
EOF
    if [ ! -f ${jobname}.pdb ] ; then
	tleap -f tleap.cmd || exit 1
    
    fi
    #convert list of resids of receptor into Calpha atom indexes
    #these atoms are restrained.
    #Indexes are shifted by 1 as they are expected to start from 0 in the OpenMM scripts
    calpha_atoms=$( awk '/^ATOM/ && $3 ~ /^CA$/ {print $2}' ${jobname}.pdb ) || exit 1
    unset restr_atoms
    for i in $calpha_atoms; do
	i1=$( expr $i - 1 )
	if [ -z "$restr_atoms" ] ; then
	    restr_atoms=$i1
	else
	    restr_atoms="${restr_atoms}, $i1"
	fi
    done
    echo "Indexes of the Calpha atoms:"
    echo "$restr_atoms"

    #get the list of receptor atoms that define the centroid of the binding site
    calphascm=()
    n=0
    for res in ${vsite_rcpt_residues[@]}; do 
	i=$( awk -v resid=$res '/^ATOM/ && $5 == resid && $3 ~ /^CA$/ {print $2}' ${jobname}.pdb ) || exit 1
	calphascm[$n]=$i
	n=$(expr $n + 1)
    done
    unset vsite_rcpt_atoms
    for i in ${calphascm[@]}; do
	i1=$( expr $i - 1 )
	if [ -z "$vsite_rcpt_atoms" ] ; then
	    vsite_rcpt_atoms=$i1
	else
	    vsite_rcpt_atoms="${vsite_rcpt_atoms}, $i1"
	fi
    done
    echo "Vsite receptor atoms:"
    echo "$vsite_rcpt_atoms"

#search for the first ligand to find the size of the receptor
    namereslig1=$( awk  'f{printf("%.3s", $8);f=0} /@<TRIPOS>ATOM/{f=1}' ${work_dir}/ligands/${lig}.mol2 ) || exit 1
    l1=$( awk "\$4 ~ /${namereslig1}/{print \$2}" < ${jobname}.pdb  | head -1 ) || exit 1
    lig1start=$(expr $l1 - 1 )
    num_atoms_rcpt=$(expr $l1 - 1 )
    #retrieve number of atoms of the ligands from their mol2 files
    num_atoms_lig1=$( awk 'NR==3{print $1}' ${work_dir}/ligands/${lig}.mol2 ) || exit 1
    
    
    #list of ligand atoms (starting from zero) assuming they are listed soon after the receptor
    lig1end=$( expr $lig1start + $num_atoms_lig1 - 1 )
    lig1_atoms=""
    for i in $( seq $lig1start $lig1end ); do
	if [ -z "$lig1_atoms" ] ; then
	    lig1_atoms=$i
	else
	    lig1_atoms="${lig1_atoms}, $i"
	fi
    done
    
    echo "atoms of $lig are $lig1_atoms"
    
   
    
    displs=""
    negative_displ=()
    count=0
    for d in ${displacement[@]}; do
	displs="$displs $d"
	count=$(expr $count + 1 )
    done
    echo "Displacement vector: $displs"

    #builds mintherm, npt, and equilibration scripts
    replstring="s#<JOBNAME>#${jobname}# ; s#<DISPLX>#${displacement[0]}# ; s#<DISPLY>#${displacement[1]}# ; s#<DISPLZ>#${displacement[2]}# ; s#<VSITERECEPTORATOMS>#${vsite_rcpt_atoms}# ; s#<RESTRAINEDATOMS>#${restr_atoms}# ; s#<LIGATOMS>#${lig1_atoms}#" 
    sed "${replstring}" < ${work_dir}/scripts/mintherm_template.py > ${jobname}_mintherm.py || exit 1
    sed "${replstring}" < ${work_dir}/scripts/equil_template.py > ${jobname}_equil.py || exit 1
    sed "${replstring}" < ${work_dir}/scripts/mdlambda_template.py > ${jobname}_mdlambda.py || exit 1
    sed "${replstring}" < ${work_dir}/scripts/asyncre_template.cntl > ${jobname}_asyncre.cntl || exit 1

    #copy runopenmm, nodefile, slurm files, etc
    cp ${work_dir}/scripts/runopenmm ${work_dir}/scripts/nodefile ${work_dir}/complexes/${jobname}/
    
    sed "s#<JOBNAME>#${jobname}#;s#<ASYNCRE_DIR>#${asyncre_dir}#" < ${work_dir}/scripts/run_template.sh > ${work_dir}/complexes/${jobname}/run.sh
    cp ${work_dir}/scripts/analyze.sh ${work_dir}/scripts/uwham_analysis.R ${work_dir}/complexes/${jobname}/
    
done

#prepare prep script
ligs=${ligands[@]}
sed "s#<RECEPTOR>#${receptor}#;s#<LIGS>#${ligs}#  ; s#<ASYNCRE_DIR>#${asyncre_dir}#g "< ${work_dir}/scripts/prep_template.sh > ${work_dir}/complexes/prep.sh

#prepare free energy calculation script
sed "s#<RECEPTOR>#${receptor}#;s#<LIGS>#${ligs}# " < ${work_dir}/scripts/free_energies_template.sh > ${work_dir}/complexes/free_energies.sh
