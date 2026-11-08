#!/bin/bash
Repoinfo=$(cat /etc/prd_info.ini |grep "Build=" | cut -d "=" -f2 | cut -d";" -f1)
REPO=${Repoinfo%*;*}
Modelinfo=$(cat /etc/prd_info.ini |grep "Model=" | cut -d "=" -f2 | cut -d";" -f1)
MODEL=${Modelinfo%*;*}
echo "__${REPO}__${MODEL}" > /proc/target_version

##UEP530x0f8c117bc99ed927f0c57aadd1ad6f965b4f14049dd309f1b7ad4316257e4d90ef7c0a38ed7fe99421a414a3f2f0c2fb01576974a6a80f4bc58ddcc9ce4d984cb60fcc422b49ee45480d0050567468b34b4042e40712d29b339aca00a8a76c0fa26499d3e5b17e1f1463ac07a4dfa5f7c5131708a56154e95d4bc7d95f49e9df00dd2bab15b1c14070a408f48fc6536a51a0e96877e9bb7580b9cff5e869ab68ddb0232c5714ca33e8a9451028194b9d0bd0cffe93c54d7485b80f0894845a1f10c0ae9895bda3892388e1c232a497d87b05de8dfa5fc2fae07301fe8f584c5fadb2e8880538473c4405d18cd2c456e48edfa6fcb0e80cab39042bd829b8d272AAACAg==:UEP