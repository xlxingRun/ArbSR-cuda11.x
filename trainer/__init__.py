import math
import os
from decimal import Decimal

import matplotlib
from utils import utility
import torch

matplotlib.use('TKAgg')


class Trainer:
    """为防止训练中断，可以保存每个epoch的训练信息
    注意：不仅要保存model.state_dict()，还需要保存对应的optimizer.state_dict()
    """
    def __init__(self, args, loader, my_model, my_loss, ckp):
        self.args = args
        self.scale_1 = args.scale_1
        self.ckp = ckp
        self.loader_train = loader.loader_train
        self.loader_test = loader.loader_test
        self.model = my_model
        self.loss = my_loss
        self.optimizer = utility.make_optimizer(args, self.model)
        self.scheduler = utility.make_scheduler(args, self.optimizer)

        """可以接着上次训练继续... """
        if self.args.resume != 0:

            self.model.get_model().load_state_dict(
                torch.load(os.path.join(ckp.dir, args.model_save, 'model_{}.pt'.format(args.resume)))
            )
            if not self.args.test_only:
                self.optimizer.load_state_dict(
                    torch.load(os.path.join(ckp.dir, args.optimizer_save, 'optimizer_{}.pt'.format(args.resume)))
                )
                self.scheduler.load_state_dict(
                    torch.load(os.path.join(ckp.dir, args.scheduler_save, 'scheduler_{}.pt'.format(args.resume)))
                )

        self.error_last = 1e8

    def train(self):
        # self.scheduler.step()

        """不太理解这里，loss.step()是什么意思"""
        self.loss.step()
        epoch = self.scheduler.last_epoch + 1
        # epoch = self.args.resume + 1

        self.loss.start_log()
        # 训练的时候使用model.train，评估的时候使用model.eval，放在for data target in dataloader外
        self.model.train()

        timer_data, timer_model = utility.Timer(), utility.Timer()

        # update self.optimizer.param_groups
        if epoch == 1:
            self.loader_train.dataset.first_epoch = True
            # adjust learning rate
            lr = 5e-5
            for param_group in self.optimizer.param_groups:
                param_group['lr'] = lr
        else:
            self.loader_train.dataset.first_epoch = False
            # adjust learning rate
            lr = self.args.lr * (2 ** -(epoch // 30))
            for param_group in self.optimizer.param_groups:
                param_group['lr'] = lr

        self.ckp.write_log('[Epoch {}]\tLearning rate: {:.2e}'.format(epoch, Decimal(lr)))

        for batch, (lr, hr, idx_scale) in enumerate(self.loader_train):
            lr, hr = self.prepare_device(lr, hr)
            scale_1 = hr.size(2) / lr.size(2)
            scale_2 = hr.size(3) / lr.size(3)
            timer_data.hold()
            self.optimizer.zero_grad()

            # inference
            self.model.get_model().set_scale(scale_1, scale_2)
            sr = self.model(lr)

            # loss function
            loss = self.loss(sr, hr)

            # backward
            if loss.item() < self.args.skip_threshold * self.error_last:
                loss.backward()
                self.optimizer.step()
            else:
                print('Skip this batch {}! (Loss: {})'.format(
                    batch+1, loss.item()
                ))
            timer_model.hold()

            if(batch + 1) % self.args.print_every == 0:
                self.ckp.write_log('[{}/{}]\t{}\t{:.1f}+{:.1f}s'.format(
                    (batch+1) * self.args.batch_size,
                    len(self.loader_train.dataset),
                    self.loss.display_loss(batch),
                    timer_model.release(),
                    timer_data.release()
                ))
            timer_data.tic()
        self.scheduler.step()

        self.loss.end_log(len(self.loader_train))
        self.error_last = self.loss.log[-1, -1]

        target = self.model.get_model()
        optimizer = self.optimizer
        scheduler = self.scheduler
        # 保存模型参数和优化器参数
        torch.save(
            target.state_dict(),
            os.path.join(self.ckp.dir, self.args.module_save, 'model_{}.pt'.format(epoch))
        )
        torch.save(
            optimizer.state_dict(),
            os.path.join(self.ckp.dir, self.args.optimizer_save, 'optimizer_{}.pt'.format(epoch))
        )
        torch.save(
            scheduler.state_dict(),
            os.path.join(self.ckp.dir, self.args.scheduler_save, 'scheduler_{}.pt'.format(epoch))
        )

    # put parameters to cpu or GPU
    def prepare_device(self, *args):
        device = torch.device('cpu' if self.args.cpu else 'cuda')

        def _prepare(tensor):
            if self.args.precision == 'half':
                tensor = tensor.half()
            return tensor.to(device)

        return [_prepare(a) for a in args]

    def terminate(self):
        if self.args.test_only:
            self.test()
            return True
        else:
            return self.scheduler.last_epoch >= self.args.epochs

    def test(self):
        self.model.eval()

        with torch.no_grad():
            for idx_scale, _ in enumerate(self.scale_1):
                print(type(self.loader_test.dataset))
                self.loader_test.dataset.set_scale(idx_scale)
                scale_1 = self.args.scale_1[idx_scale]
                scale_2 = self.args.scale_2[idx_scale]

                """两个图像质量评价指标"""
                """PSNR：峰值信噪比"""
                eval_psnr = 0
                """SSIM：结构相似性"""
                eval_ssim = 0

                for idx_img, (lr, hr, filename) in enumerate(self.loader_test):
                    filename = filename[0]

                    # prepare LR & HR images
                    no_eval = (hr.nelement() == 1)
                    if not no_eval:
                        lr, hr = self.prepare_device(lr, hr)
                    else:
                        lr = self.prepare_device(lr)
                        
                    lr, hr = self.crop_border(lr, hr, scale_1, scale_2)

                    # inference
                    self.model.get_model().set_scale(scale_1, scale_2)
                    sr = self.model(lr)

                    # evaluation
                    sr = utility.quantize(sr, self.args.rgb_range)
                    save_list = [sr]

                    if not no_eval:
                        eval_psnr += utility.calc_psnr(
                            sr, hr, [scale_1, scale_2], self.args.rgb_range,
                            benchmark=self.loader_test.dataset.benchmark
                        )
                        eval_ssim += utility.calc_ssim(
                            sr, hr, [scale_1, scale_2],
                            benchmark=self.loader_test.dataset.benchmark
                        )

                    # save SR results
                    if self.args.save_results:
                        self.ckp.save_results(filename, save_list, scale_1)

                if scale_1 == scale_2:
                    print('[{} x{}]\tPSNR: {:.3f} SSIM: {:.4f}'.format(
                        self.args.data_test,
                        scale_1,
                        eval_psnr / len(self.loader_test),
                        eval_ssim / len(self.loader_test)
                    ))
                else:
                    print('[{} x{}/x{}]\tPSNR: {:.3f} SSIM: {:.4f}'.format(
                        self.args.data_test,
                        scale_1,
                        scale_2,
                        eval_psnr / len(self.loader_test),
                        eval_ssim / len(self.loader_test)
                    ))

    @staticmethod
    def crop_border(img_lr, img_hr, scale_1, scale_2):
        _, _, height_lr, width_lr = img_lr.size()
        _, _, height_hr, width_hr = img_hr.size()

        height = height_lr if round(height_lr * scale_1) <= height_hr else math.floor(height_hr / scale_1)
        width = width_lr if round(width_lr * scale_2) <= width_hr else math.floor(width_hr / scale_2)

        step = list()
        for s in [scale_1, scale_2]:
            for i in [1, 2, 5, 10, 20, 50]:
                if (s*i) == int(s*i):
                    step.append(i)
                    break

        height_new = height // step[0] * step[0]
        if height_new % 2 == 1:
            height_new = height // (step[0]*2) * step[0] * 2

        width_new = width // step[1] * step[1]
        if width_new % 2 == 1:
            width_new = width // (step[1]*2) * step[1] * 2

        img_lr = img_lr[:, :, :height_new, :width_new]
        img_hr = img_hr[:, :, :round(scale_1*height_new), :round(scale_2*width_new)]

        return img_lr, img_hr
