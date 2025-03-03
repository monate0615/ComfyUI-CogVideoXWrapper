import os
import torch
import torch.nn as nn
import json
import folder_paths
import comfy.model_management as mm

from diffusers.models import AutoencoderKLCogVideoX
from diffusers.schedulers import CogVideoXDDIMScheduler
from .custom_cogvideox_transformer_3d import CogVideoXTransformer3DModel
from .pipeline_cogvideox import CogVideoXPipeline
from contextlib import nullcontext

from .cogvideox_fun.transformer_3d import CogVideoXTransformer3DModel as CogVideoXTransformer3DModelFun
from .cogvideox_fun.fun_pab_transformer_3d import CogVideoXTransformer3DModel as CogVideoXTransformer3DModelFunPAB
from .cogvideox_fun.autoencoder_magvit import AutoencoderKLCogVideoX as AutoencoderKLCogVideoXFun

from .cogvideox_fun.pipeline_cogvideox_inpaint import CogVideoX_Fun_Pipeline_Inpaint
from .cogvideox_fun.pipeline_cogvideox_control import CogVideoX_Fun_Pipeline_Control

from .videosys.cogvideox_transformer_3d import CogVideoXTransformer3DModel as CogVideoXTransformer3DModelPAB

from .utils import check_diffusers_version, remove_specific_blocks, log
from comfy.utils import load_torch_file

script_directory = os.path.dirname(os.path.abspath(__file__))

class DownloadAndLoadCogVideoModel:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": (
                    [
                        "THUDM/CogVideoX-2b",
                        "THUDM/CogVideoX-5b",
                        "THUDM/CogVideoX-5b-I2V",
                        "bertjiazheng/KoolCogVideoX-5b",
                        "kijai/CogVideoX-Fun-2b",
                        "kijai/CogVideoX-Fun-5b",
                        "kijai/CogVideoX-5b-Tora",
                        "alibaba-pai/CogVideoX-Fun-V1.1-2b-InP",
                        "alibaba-pai/CogVideoX-Fun-V1.1-5b-InP",
                        "alibaba-pai/CogVideoX-Fun-V1.1-2b-Pose",
                        "alibaba-pai/CogVideoX-Fun-V1.1-5b-Pose",
                        "feizhengcong/CogvideoX-Interpolation",
                        "NimVideo/cogvideox-2b-img2vid"
                    ],
                ),

            },
            "optional": {
                "precision": (["fp16", "fp32", "bf16"],
                    {"default": "bf16", "tooltip": "official recommendation is that 2b model should be fp16, 5b model should be bf16"}
                ),
                "fp8_transformer": (['disabled', 'enabled', 'fastmode'], {"default": 'disabled', "tooltip": "enabled casts the transformer to torch.float8_e4m3fn, fastmode is only for latest nvidia GPUs and requires torch 2.4.0 and cu124 minimum"}),
                "compile": (["disabled","onediff","torch"], {"tooltip": "compile the model for faster inference, these are advanced options only available on Linux, see readme for more info"}),
                "enable_sequential_cpu_offload": ("BOOLEAN", {"default": False, "tooltip": "significantly reducing memory usage and slows down the inference"}),
                "pab_config": ("PAB_CONFIG", {"default": None}),
                "block_edit": ("TRANSFORMERBLOCKS", {"default": None}),
                "lora": ("COGLORA", {"default": None}),
            }
        }

    RETURN_TYPES = ("COGVIDEOPIPE",)
    RETURN_NAMES = ("cogvideo_pipe", )
    FUNCTION = "loadmodel"
    CATEGORY = "CogVideoWrapper"
    DESCRIPTION = "Downloads and loads the selected CogVideo model from Huggingface to 'ComfyUI/models/CogVideo'"

    def loadmodel(self, model, precision, fp8_transformer="disabled", compile="disabled", enable_sequential_cpu_offload=False, pab_config=None, block_edit=None, lora=None):
        
        check_diffusers_version()

        device = mm.get_torch_device()
        offload_device = mm.unet_offload_device()
        mm.soft_empty_cache()

        dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[precision]
        download_path = folder_paths.get_folder_paths("CogVideo")[0]
        
        if "Fun" in model:
            if not "1.1" in model:
                repo_id = "kijai/CogVideoX-Fun-pruned"
                if "2b" in model:
                    base_path = os.path.join(folder_paths.models_dir, "CogVideoX_Fun", "CogVideoX-Fun-2b-InP") # location of the official model
                    if not os.path.exists(base_path):
                        base_path = os.path.join(download_path, "CogVideoX-Fun-2b-InP")
                elif "5b" in model:
                    base_path = os.path.join(folder_paths.models_dir, "CogVideoX_Fun", "CogVideoX-Fun-5b-InP") # location of the official model
                    if not os.path.exists(base_path):
                        base_path = os.path.join(download_path, "CogVideoX-Fun-5b-InP")
            elif "1.1" in model:
                repo_id = model
                base_path = os.path.join(folder_paths.models_dir, "CogVideoX_Fun", (model.split("/")[-1])) # location of the official model
                if not os.path.exists(base_path):
                    base_path = os.path.join(download_path, (model.split("/")[-1]))
                download_path = base_path

        elif "2b" in model:
            if 'img2vid' in model:
                base_path = os.path.join(download_path, "cogvideox-2b-img2vid")
                download_path = base_path
                repo_id = model
            else:
                base_path = os.path.join(download_path, "CogVideo2B")
                download_path = base_path
                repo_id = model
        else:
            base_path = os.path.join(download_path, (model.split("/")[-1]))
            download_path = base_path
            repo_id = model
            

        if "2b" in model:
            scheduler_path = os.path.join(script_directory, 'configs', 'scheduler_config_2b.json')
        else:
            scheduler_path = os.path.join(script_directory, 'configs', 'scheduler_config_5b.json')
        
        if not os.path.exists(base_path) or not os.path.exists(os.path.join(base_path, "transformer")):
            log.info(f"Downloading model to: {base_path}")
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id=repo_id,
                ignore_patterns=["*text_encoder*", "*tokenizer*"],
                local_dir=download_path,
                local_dir_use_symlinks=False,
            )

        # transformer
        if "Fun" in model:
            if pab_config is not None:
                transformer = CogVideoXTransformer3DModelFunPAB.from_pretrained(base_path, subfolder="transformer")
            else:
                transformer = CogVideoXTransformer3DModelFun.from_pretrained(base_path, subfolder="transformer")
        else:
            if pab_config is not None:
                transformer = CogVideoXTransformer3DModelPAB.from_pretrained(base_path, subfolder="transformer")
            else:
                transformer = CogVideoXTransformer3DModel.from_pretrained(base_path, subfolder="transformer")
        
        transformer = transformer.to(dtype).to(offload_device)

        #LoRAs
        if lora is not None:
            from .lora_utils import merge_lora, load_lora_into_transformer
            if "fun" in model.lower():
                for l in lora:
                    log.info(f"Merging LoRA weights from {l['path']} with strength {l['strength']}")
                    transformer = merge_lora(transformer, l["path"], l["strength"])
            else:
                transformer = load_lora_into_transformer(lora, transformer)
                        
                
        if block_edit is not None:
            transformer = remove_specific_blocks(transformer, block_edit)
        
        #fp8
        if fp8_transformer == "enabled" or fp8_transformer == "fastmode":
            for name, param in transformer.named_parameters():
                params_to_keep = {"patch_embed", "lora", "pos_embedding"}
                if not any(keyword in name for keyword in params_to_keep):
                    param.data = param.data.to(torch.float8_e4m3fn)
        
            if fp8_transformer == "fastmode":
                from .fp8_optimization import convert_fp8_linear
                convert_fp8_linear(transformer, dtype)

        with open(scheduler_path) as f:
            scheduler_config = json.load(f)
        scheduler = CogVideoXDDIMScheduler.from_config(scheduler_config)     

        # VAE
        if "Fun" in model:
            vae = AutoencoderKLCogVideoXFun.from_pretrained(base_path, subfolder="vae").to(dtype).to(offload_device)
            if "Pose" in model:
                pipe = CogVideoX_Fun_Pipeline_Control(vae, transformer, scheduler, pab_config=pab_config)
            else:
                pipe = CogVideoX_Fun_Pipeline_Inpaint(vae, transformer, scheduler, pab_config=pab_config)
        else:
            vae = AutoencoderKLCogVideoX.from_pretrained(base_path, subfolder="vae").to(dtype).to(offload_device)
            pipe = CogVideoXPipeline(vae, transformer, scheduler, pab_config=pab_config)
            if "cogvideox-2b-img2vid" in model:
                pipe.input_with_padding = False 

        if enable_sequential_cpu_offload:
            pipe.enable_sequential_cpu_offload()

        # compilation
        if compile == "torch":
            torch._dynamo.config.suppress_errors = True
            pipe.transformer.to(memory_format=torch.channels_last)
            #pipe.transformer = torch.compile(pipe.transformer, mode="default", fullgraph=False, backend="inductor")
            for i, block in enumerate(pipe.transformer.transformer_blocks):
                if "CogVideoXBlock" in str(block):
                    pipe.transformer.transformer_blocks[i] = torch.compile(block, fullgraph=False, dynamic=False, backend="inductor")
        elif compile == "onediff":
            from onediffx import compile_pipe
            os.environ['NEXFORT_FX_FORCE_TRITON_SDPA'] = '1'
            
            pipe = compile_pipe(
            pipe,
            backend="nexfort",
            options= {"mode": "max-optimize:max-autotune:max-autotune", "memory_format": "channels_last", "options": {"inductor.optimize_linear_epilogue": False, "triton.fuse_attention_allow_fp16_reduction": False}},
            ignores=["vae"],
            fuse_qkv_projections=True if pab_config is None else False,
            )

        pipeline = {
            "pipe": pipe,
            "dtype": dtype,
            "base_path": base_path,
            "onediff": True if compile == "onediff" else False,
            "cpu_offloading": enable_sequential_cpu_offload,
            "scheduler_config": scheduler_config,
            "model_name": model
        }

        return (pipeline,)

class DownloadAndLoadCogVideoGGUFModel:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": (
                    [
                        "CogVideoX_5b_GGUF_Q4_0.safetensors",
                        "CogVideoX_5b_I2V_GGUF_Q4_0.safetensors",
                        "CogVideoX_5b_fun_GGUF_Q4_0.safetensors",
                        "CogVideoX_5b_fun_1_1_GGUF_Q4_0.safetensors",
                        "CogVideoX_5b_fun_1_1_Pose_GGUF_Q4_0.safetensors",
                        "CogVideoX_5b_Interpolation_GGUF_Q4_0.safetensors",
                        "CogVideoX_5b_Tora_GGUF_Q4_0.safetensors",

                    ],
                ),
            "vae_precision": (["fp16", "fp32", "bf16"], {"default": "bf16", "tooltip": "VAE dtype"}),
            "fp8_fastmode": ("BOOLEAN", {"default": False, "tooltip": "only supported on 4090 and later GPUs, also requires torch 2.4.0 with cu124 minimum"}),
            "load_device": (["main_device", "offload_device"], {"default": "main_device"}),
            "enable_sequential_cpu_offload": ("BOOLEAN", {"default": False, "tooltip": "significantly reducing memory usage and slows down the inference"}),
            },
            "optional": {
                "pab_config": ("PAB_CONFIG", {"default": None}),
                "block_edit": ("TRANSFORMERBLOCKS", {"default": None}),
                "compile": (["disabled","torch"], {"tooltip": "compile the model for faster inference, these are advanced options only available on Linux, see readme for more info"}),
              
            }
        }

    RETURN_TYPES = ("COGVIDEOPIPE",)
    RETURN_NAMES = ("cogvideo_pipe", )
    FUNCTION = "loadmodel"
    CATEGORY = "CogVideoWrapper"

    def loadmodel(self, model, vae_precision, fp8_fastmode, load_device, enable_sequential_cpu_offload, pab_config=None, block_edit=None, compile="disabled"):

        check_diffusers_version()

        device = mm.get_torch_device()
        offload_device = mm.unet_offload_device()
        mm.soft_empty_cache()

        vae_dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[vae_precision]
        download_path = os.path.join(folder_paths.models_dir, 'CogVideo', 'GGUF')
        gguf_path = os.path.join(folder_paths.models_dir, 'diffusion_models', model) # check MinusZone's model path first
        if not os.path.exists(gguf_path):
            gguf_path = os.path.join(download_path, model)
            if not os.path.exists(gguf_path):
                if "I2V" in model or "1_1" in model or "Interpolation" in model or "Tora" in model:
                    repo_id = "Kijai/CogVideoX_GGUF"
                else:
                    repo_id = "MinusZoneAI/ComfyUI-CogVideoX-MZ"
                log.info(f"Downloading model to: {gguf_path}")
                from huggingface_hub import snapshot_download

                snapshot_download(
                    repo_id=repo_id,
                    allow_patterns=[f"*{model}*"],
                    local_dir=download_path,
                    local_dir_use_symlinks=False,
                )
        
        if "5b" in model:
            scheduler_path = os.path.join(script_directory, 'configs', 'scheduler_config_5b.json')
            transformer_path = os.path.join(script_directory, 'configs', 'transformer_config_5b.json')
        elif "2b" in model:
            scheduler_path = os.path.join(script_directory, 'configs', 'scheduler_config_2b.json')
            transformer_path = os.path.join(script_directory, 'configs', 'transformer_config_2b.json')
    
        with open(transformer_path) as f:
            transformer_config = json.load(f)

        sd = load_torch_file(gguf_path)

        from . import mz_gguf_loader
        import importlib
        importlib.reload(mz_gguf_loader)

        with mz_gguf_loader.quantize_lazy_load():
            if "fun" in model:
                if "Pose" in model:
                    transformer_config["in_channels"] = 32
                else:
                    transformer_config["in_channels"] = 33
                if pab_config is not None:
                    transformer = CogVideoXTransformer3DModelFunPAB.from_config(transformer_config)
                else:
                    transformer = CogVideoXTransformer3DModelFun.from_config(transformer_config)
            elif "I2V" in model or "Interpolation" in model:
                transformer_config["in_channels"] = 32
                if pab_config is not None:
                    transformer = CogVideoXTransformer3DModelPAB.from_config(transformer_config)
                else:
                    transformer = CogVideoXTransformer3DModel.from_config(transformer_config)
            else:
                transformer_config["in_channels"] = 16
                if pab_config is not None:
                    transformer = CogVideoXTransformer3DModelPAB.from_config(transformer_config)
                else:
                    transformer = CogVideoXTransformer3DModel.from_config(transformer_config)

            if "2b" in model:
                for name, param in transformer.named_parameters():
                    if name != "pos_embedding":
                        param.data = param.data.to(torch.float8_e4m3fn)
                    else:
                        param.data = param.data.to(torch.float16)
            else:
                transformer.to(torch.float8_e4m3fn)

            if block_edit is not None:
                transformer = remove_specific_blocks(transformer, block_edit)

            transformer = mz_gguf_loader.quantize_load_state_dict(transformer, sd, device="cpu")
            if load_device == "offload_device":
                transformer.to(offload_device)
            else:
                transformer.to(device)
        
        
        if fp8_fastmode:
           from .fp8_optimization import convert_fp8_linear
           convert_fp8_linear(transformer, vae_dtype)

        if compile == "torch":
            # compilation
            for i, block in enumerate(transformer.transformer_blocks):
                    transformer.transformer_blocks[i] = torch.compile(block, fullgraph=False, dynamic=False, backend="inductor")
        with open(scheduler_path) as f:
            scheduler_config = json.load(f)
        
        scheduler = CogVideoXDDIMScheduler.from_config(scheduler_config, subfolder="scheduler")

        # VAE
        vae_dl_path = os.path.join(folder_paths.models_dir, 'CogVideo', 'VAE')
        vae_path = os.path.join(vae_dl_path, "cogvideox_vae.safetensors")
        if not os.path.exists(vae_path):
            log.info(f"Downloading VAE model to: {vae_path}")
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id="Kijai/CogVideoX-Fun-pruned",
                allow_patterns=["*cogvideox_vae.safetensors*"],
                local_dir=vae_dl_path,
                local_dir_use_symlinks=False,
            )
        with open(os.path.join(script_directory, 'configs', 'vae_config.json')) as f:
            vae_config = json.load(f)
        
        vae_sd = load_torch_file(vae_path)
        if "fun" in model:
            vae = AutoencoderKLCogVideoXFun.from_config(vae_config).to(vae_dtype).to(offload_device)
            vae.load_state_dict(vae_sd)
            if "Pose" in model:
                pipe = CogVideoX_Fun_Pipeline_Control(vae, transformer, scheduler, pab_config=pab_config)
            else:
                pipe = CogVideoX_Fun_Pipeline_Inpaint(vae, transformer, scheduler, pab_config=pab_config)
        else:
            vae = AutoencoderKLCogVideoX.from_config(vae_config).to(vae_dtype).to(offload_device)
            vae.load_state_dict(vae_sd)
            pipe = CogVideoXPipeline(vae, transformer, scheduler, pab_config=pab_config)

        if enable_sequential_cpu_offload:
            pipe.enable_sequential_cpu_offload()

        pipeline = {
            "pipe": pipe,
            "dtype": vae_dtype,
            "base_path": model,
            "onediff": False,
            "cpu_offloading": enable_sequential_cpu_offload,
            "scheduler_config": scheduler_config,
            "model_name": model
        }

        return (pipeline,)

class DownloadAndLoadToraModel:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": (
                    [
                        "kijai/CogVideoX-5b-Tora",
                    ],
                ),
            },
        }

    RETURN_TYPES = ("TORAMODEL",)
    RETURN_NAMES = ("tora_model", )
    FUNCTION = "loadmodel"
    CATEGORY = "CogVideoWrapper"
    DESCRIPTION = "Downloads and loads the the Tora model from Huggingface to 'ComfyUI/models/CogVideo/CogVideoX-5b-Tora'"

    def loadmodel(self, model):
        
        check_diffusers_version()

        device = mm.get_torch_device()
        offload_device = mm.unet_offload_device()
        mm.soft_empty_cache()
  
        download_path = folder_paths.get_folder_paths("CogVideo")[0]

        from .tora.traj_module import MGF

        try:
            from accelerate import init_empty_weights
            from accelerate.utils import set_module_tensor_to_device
            is_accelerate_available = True
        except:
            is_accelerate_available = False
            pass

        download_path = os.path.join(folder_paths.models_dir, 'CogVideo', "CogVideoX-5b-Tora")
        fuser_path = os.path.join(download_path, "fuser", "fuser.safetensors")
        if not os.path.exists(fuser_path):
            log.info(f"Downloading Fuser model to: {fuser_path}")
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id=model,
                allow_patterns=["*fuser.safetensors*"],
                local_dir=download_path,
                local_dir_use_symlinks=False,
            )

        hidden_size = 3072
        num_layers = 42

        with (init_empty_weights() if is_accelerate_available else nullcontext()):
            fuser_list = nn.ModuleList([MGF(128, hidden_size) for _ in range(num_layers)])
        
        fuser_sd = load_torch_file(fuser_path)
        if is_accelerate_available:
            for key in fuser_sd:
                set_module_tensor_to_device(fuser_list, key, dtype=torch.float16, device=device, value=fuser_sd[key])
        else:
            fuser_list.load_state_dict(fuser_sd)
            for module in fuser_list:
                for param in module.parameters():
                    param.data = param.data.to(torch.bfloat16).to(device)
        del fuser_sd

        traj_extractor_path = os.path.join(download_path, "traj_extractor", "traj_extractor.safetensors")
        if not os.path.exists(traj_extractor_path):
            log.info(f"Downloading trajectory extractor model to: {traj_extractor_path}")
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id="kijai/CogVideoX-5b-Tora",
                allow_patterns=["*traj_extractor.safetensors*"],
                local_dir=download_path,
                local_dir_use_symlinks=False,
            )

        from .tora.traj_module import TrajExtractor
        with (init_empty_weights() if is_accelerate_available else nullcontext()):
            traj_extractor = TrajExtractor(
                vae_downsize=(4, 8, 8),
                patch_size=2,
                nums_rb=2,
                cin=16,
                channels=[128] * 42,
                sk=True,
                use_conv=False,
            )
    
        traj_sd = load_torch_file(traj_extractor_path)
        if is_accelerate_available:
            for key in traj_sd:
                set_module_tensor_to_device(traj_extractor, key, dtype=torch.float32, device=device, value=traj_sd[key])
        else:
            traj_extractor.load_state_dict(traj_sd)
            traj_extractor.to(torch.float32).to(device)

        toramodel = {
            "fuser_list": fuser_list,
            "traj_extractor": traj_extractor,
        }

        return (toramodel,)

class DownloadAndLoadCogVideoControlNet:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": (
                    [
                        "TheDenk/cogvideox-2b-controlnet-hed-v1",
                        "TheDenk/cogvideox-2b-controlnet-canny-v1",
                        "TheDenk/cogvideox-5b-controlnet-hed-v1",
                        "TheDenk/cogvideox-5b-controlnet-canny-v1"
                    ],
                ),

            },
        }

    RETURN_TYPES = ("COGVIDECONTROLNETMODEL",)
    RETURN_NAMES = ("cogvideo_controlnet", )
    FUNCTION = "loadmodel"
    CATEGORY = "CogVideoWrapper"

    def loadmodel(self, model):
        from .cogvideo_controlnet import CogVideoXControlnet

        device = mm.get_torch_device()
        offload_device = mm.unet_offload_device()
        mm.soft_empty_cache()

        
        download_path = os.path.join(folder_paths.models_dir, 'CogVideo', 'ControlNet')
        base_path = os.path.join(download_path, (model.split("/")[-1]))
        
        if not os.path.exists(base_path):
            log.info(f"Downloading model to: {base_path}")
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id=model,
                ignore_patterns=["*text_encoder*", "*tokenizer*"],
                local_dir=base_path,
                local_dir_use_symlinks=False,
            )

        controlnet = CogVideoXControlnet.from_pretrained(base_path)

        return (controlnet,)
    
NODE_CLASS_MAPPINGS = {
    "DownloadAndLoadCogVideoModel": DownloadAndLoadCogVideoModel,
    "DownloadAndLoadCogVideoGGUFModel": DownloadAndLoadCogVideoGGUFModel,
    "DownloadAndLoadCogVideoControlNet": DownloadAndLoadCogVideoControlNet,
    "DownloadAndLoadToraModel": DownloadAndLoadToraModel,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "DownloadAndLoadCogVideoModel": "(Down)load CogVideo Model",
    "DownloadAndLoadCogVideoGGUFModel": "(Down)load CogVideo GGUF Model",
    "DownloadAndLoadCogVideoControlNet": "(Down)load CogVideo ControlNet",
    "DownloadAndLoadToraModel": "(Down)load Tora Model",
    }