'''
{
    "the-talmud/video-16b-mah-hatzad-applying-a-rabbinic-logical-tool-NvAdj": [
        "Javanese.srt",
        "ptPt.srt",
        "Spanish.srt",
        "English.srt",
        "Russian.srt",
        "Romanian.srt",
        "French.srt"
    ],
    "the-talmud/video-23-parallel-narratives-pngb7": [
        "Javanese.srt",
        "ptPt.srt",
        "Spanish.srt",
        "English.srt",
        "Russian.srt",
        "Romanian.srt",
        "French.srt"
    ],'''

import json
from pathlib import Path
from tqdm import tqdm

import torch
from torch.utils.data import DataLoader

from data_loader import CourseraDataset, collate_fn
from audio_processor import AudioProcessor


if __name__ == "__main__":
    # Device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    torch_dtype = None#torch.bfloat16#
    print(f"Device: {device}")

    print('Dataset - START')
    tokenizer_path = Path('/home/nofreewill/Documents/Projects/Audio2Text_archived/tokenizer/')
    ds_path = Path('/media/nofreewill/8TB-SSD/Audio/Coursera/courses/')
    langs_dict_path = Path('/home/nofreewill/Documents/Projects/PrepareCoursera/langs_dict.json')
    langs_dict = json.loads(langs_dict_path.read_text())
    episodes_list = [k for k,v in langs_dict.items() if v == 'en']
    
    
    ds = CourseraDataset(ds_path, episodes_list, tokenizer_path, T=240.000)
    dl = DataLoader(ds, batch_size=1, shuffle=True, collate_fn=collate_fn, num_workers=0)
    print('Dataset - END')

    print('AudioProcessor info - START')
    audio_proc = AudioProcessor()
    audio_proc.print_info()
    print('AudioProcessor info - END')

    print('Model - START')
    from model_architecture.model import XModel
    n_bbpe = ds.bbpe_tokenizer.get_vocab_size()
    model = XModel(n_bbpe=n_bbpe, n_layers=16, d_model=1024, d_ff=2048, n_heads=16, dropout=0.0)
    model = model.to(device, dtype=torch_dtype)
    print('Model - END')

    print("Training - START")
    lr = 3e-3
    n_grad_acc = 32
    start_epoch = 0
    epochs_num = 10
    weight_decay = 1e-2
    pct_start = 0.1/epochs_num
    ctc_loss_fn = torch.nn.CTCLoss(blank=0, zero_infinity=True)
    optimizer = torch.optim.AdamW(model.parameters(), weight_decay=weight_decay)
    lr_sched = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=lr,
                                                steps_per_epoch=len(dl)+2, epochs=epochs_num,
                                                div_factor=25, final_div_factor=1e4, pct_start=pct_start)
    scaler = torch.cuda.amp.GradScaler()
    # Stats
    import time
    stats_path = Path(f'training_stats/{time.strftime("%Y%m%d_%H%M%S")}')
    stats_path.mkdir(parents=True, exist_ok=True)
    train_csv_path = Path(f'{stats_path}/train.csv')
    w_trn = open(str(train_csv_path), 'w', buffering=1)
    save_every_step = 1000
    step_counter = 0
    model_save_path = Path('model_weights')
    model_save_path.mkdir(parents=True, exist_ok=True)

    for epoch_num in range(epochs_num):
        if epoch_num < start_epoch:
            for _ in range(len(dl)):
                lr_sched.step()
            continue
        pbar = tqdm(dl)
        for i, batch in enumerate(pbar):
            if batch == None:
                continue
            audios_tensor, audio_lens, bbpes_tensor, bbpe_lens = batch
            # Move to device
            audios_tensor, audio_lens = audios_tensor.to(device), audio_lens.to(device)
            bbpes_tensor, bbpe_lens = bbpes_tensor.to(device), bbpe_lens.to(device)
            input_tensor = bbpes_tensor[:, :-1]
            target_tensor = bbpes_tensor[:, 1:]
            with torch.no_grad():
                log_mels_tensor, log_mel_lens = audio_proc.process(audios_tensor, audio_lens)
                log_mels_tensor = log_mels_tensor.to(dtype=torch_dtype)
            
            # Forward
            with torch.cuda.amp.autocast(torch_dtype == None):
                enc_out, enc_lens = model(log_mels_tensor, log_mel_lens, input_tensor)
                enc_log_probs = torch.log_softmax(enc_out, dim=-1)#.float()  # "ctc_loss_cuda" not implemented for 'BFloat16'
                ctc_tgt = bbpes_tensor.clone()
                ctc_tgt[(ctc_tgt==1) | (ctc_tgt==2)] = 0
                enc_loss = ctc_loss_fn(enc_log_probs.transpose(0,1), ctc_tgt, enc_lens, bbpe_lens)  # ctclossfn is wrong so correct it here
                loss = enc_loss
                if loss.item()>20:
                    continue
                loss = loss / n_grad_acc
            # Backward
            scaler.scale(loss).backward()
            if (i+1) % n_grad_acc == n_grad_acc-1:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad()
                if step_counter % save_every_step == 0:
                    torch.save(model.state_dict(), f'{model_save_path}/model_{step_counter}.pt')
                step_counter += 1
            lr_sched.step()

            # Record
            pbar.set_description(f'epoch: {epoch_num}, enc: {enc_loss.item():.2f}, lr: {lr_sched.get_last_lr()[0]:.4e}')
            w_trn.write(f'{epoch_num},{i},{lr_sched.get_last_lr()[0]:.4e},{enc_loss.item():.4f}\n')
        
    print("Training - END")

    
    print(None)