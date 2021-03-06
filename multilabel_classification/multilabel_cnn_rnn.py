# -*- coding: utf-8 -*-
"""
Created on Wed May  2 12:36:42 2018

@author: Administrator
"""

import os
import json
import time
import shutil
import pickle
import logging
from cnn_rnn_model import data_helper_multi
import numpy as np
import tensorflow as tf
import pandas as pd
from cnn_rnn_model.text_cnn_rnn import TextCNNRNN
from sklearn.model_selection import train_test_split
from skmultilearn.adapt import MLkNN

logging.getLogger().setLevel(logging.INFO)

def train_cnn_rnn(input_file, training_config):
    # read data and params
    x_, y_, vocabulary, vocabulary_inv, df, label_dict=data_helper_multi.load_data(input_file)
    params=json.loads(open(training_config).read())
    
    # create a directory, everything related to the training will be saved in this directory
    timestamp=str(int(time.time()))
    output_dir=os.path.join('data_path_save','cnn_rnn_'+timestamp)
    trained_dir=os.path.join(output_dir,'trained_results')
    if os.path.exists(trained_dir):
        shutil.rmtree(trained_dir)
    os.makedirs(trained_dir)
    
    # assign a 300 dimension vector to each word
    word_embeddings=data_helper_multi.load_embeddings(vocabulary)
    embedding_mat=[word_embeddings[word] for index,word in enumerate(vocabulary_inv)]
    embedding_mat=np.array(embedding_mat, dtype=np.float32)
    
    # split the original dataset into trainset and devset
    x_train, x_dev, y_train, y_dev=train_test_split(x_, y_, test_size=0.1)
    # split the trainset into trainset and devset
    logging.info('x_train: {}, x_dev: {}'.format(len(x_train), len(x_dev)))
    
    graph=tf.Graph()
    with graph.as_default():
        session_conf=tf.ConfigProto(allow_soft_placement=True, log_device_placement=False)
        sess=tf.Session(config=session_conf)
        with sess.as_default():
            cnn_rnn=TextCNNRNN(embedding_mat=embedding_mat, sequence_length=x_train.shape[1], num_classes=y_train.shape[1], 
                               non_static=params['non_static'], hidden_unit=params['hidden_unit'], max_pool_size=params['max_pool_size'],
                               filter_sizes=map(int, params['filter_sizes'].split(",")), num_filters=params['num_filters'],
                               embedding_size=params['embedding_dim'], l2_reg_lambda=params['l2_reg_lambda'])
            global_step=tf.Variable(0, name='global_step', trainable=False)
            optimizer=tf.train.RMSPropOptimizer(1e-3, decay=0.9)
            grads_and_vars=optimizer.compute_gradients(cnn_rnn.loss)
            train_op=optimizer.apply_gradients(grads_and_vars, global_step=global_step)
            checkpoint_dir=os.path.join(output_dir,'checkpoints')
            if os.path.exists(checkpoint_dir):
                shutil.rmtree(checkpoint_dir)
            os.makedirs(checkpoint_dir)
            checkpoint_prefix=os.path.join(checkpoint_dir, 'model')
            
            def real_len(batches):
                return [np.ceil(np.argmin(batch+[0])*1.0/params['max_pool_size']) for batch in batches]
            
            def train_step(x_batch, y_batch):
                feed_dict={
                        cnn_rnn.input_x: x_batch, 
                        cnn_rnn.input_y: y_batch,
                        cnn_rnn.dropout_keep_prob: params['dropout_keep_prob'],
                        cnn_rnn.batch_size: len(x_batch),
                        cnn_rnn.pad: np.zeros([len(x_batch), 1, params['embedding_dim'], 1]),
                        cnn_rnn.real_len: real_len(x_batch)
                        }
                _, step, loss, scores=sess.run([train_op, global_step, cnn_rnn.loss, cnn_rnn.scores], feed_dict=feed_dict)
                return scores
                
            def dev_step(x_batch, y_batch):
                feed_dict={
                        cnn_rnn.input_x: x_batch, 
                        cnn_rnn.input_y: y_batch,
                        cnn_rnn.dropout_keep_prob: 1.0,
                        cnn_rnn.batch_size: len(x_batch),
                        cnn_rnn.pad: np.zeros([len(x_batch), 1, params['embedding_dim'], 1]),
                        cnn_rnn.real_len: real_len(x_batch)
                        }
                step, loss, scores=sess.run([global_step, cnn_rnn.loss, cnn_rnn.scores], feed_dict=feed_dict)
                return step, loss, scores
            
            saver=tf.train.Saver()
            sess.run(tf.global_variables_initializer())
            
            # training starts here
            train_batches=data_helper_multi.batch_iter(list(zip(x_train, y_train)), params['batch_size'], params['num_epochs'])
            best_accuracy, best_at_step=0, 0
            x_train_fit=np.zeros([params['batch_size']*params['evaluate_every'], len(label_dict.items())])
            y_train_fit=np.zeros([params['batch_size']*params['evaluate_every'], len(label_dict.items())])
            for train_batch in train_batches:
                x_train_batch, y_train_batch=zip(*train_batch)
                scores=train_step(x_train_batch, y_train_batch)
                current_step=tf.train.global_step(sess, global_step)
                x_train_fit[(current_step%params['evaluate_every'])*params['batch_size']: 
                    (current_step%params['evaluate_every'])*params['batch_size']+params['batch_size']]=scores
                y_train_fit[(current_step%params['evaluate_every'])*params['batch_size']: 
                    (current_step%params['evaluate_every'])*params['batch_size']+params['batch_size']]=y_train_batch
                
                if current_step%params['evaluate_every']==0:
                    clf=MLkNN(k=4)
                    clf.fit(x_train_fit, y_train_fit)
                    dev_batches=data_helper_multi.batch_iter(list(zip(x_dev, y_dev)), params['batch_size'], 1)
                    total_batches_dev=len(x_dev)//params['batch_size']
                    x_dev_fit=np.zeros([params['batch_size']*total_batches_dev, len(label_dict.items())])
                    y_dev_fit=np.zeros([params['batch_size']*total_batches_dev, len(label_dict.items())])
                    for step_dev, dev_batch in enumerate(dev_batches):
                        x_dev_batch, y_dev_batch=zip(*dev_batch)
                        step, loss, scores=dev_step(x_dev_batch, y_dev_batch)
                        x_dev_fit[step_dev*params['batch_size']: step_dev*params['batch_size']+params['batch_size']]=scores
                        y_dev_fit[step_dev*params['batch_size']: step_dev*params['batch_size']+params['batch_size']]=y_dev_batch
                    y_dev_preds=clf.predict(x_dev_fit)
                    y_dev_preds=y_dev_preds.toarray()
                    y_union=y_dev_preds+y_dev_fit
                    accuracy=float(np.sum(y_union==2))/float(np.sum(y_union==1)+np.sum(y_union==2))
                    precision=float(np.sum(y_union==2))/float(np.sum(y_dev_preds==1))
                    recall=float(np.sum(y_union==2))/float(np.sum(y_dev_fit==1))
                    f1=2*precision*recall/(precision+recall)
                    logging.info('Accuracy on dev set: {}'.format(accuracy))
                    logging.info('Precision on dev set: {}'.format(precision))
                    logging.info('Recall on dev set: {}'.format(recall))
                    logging.info('F1-measure on dev set: {}'.format(f1))
                    
                    if accuracy>=best_accuracy:
                        best_accuracy, best_at_step=accuracy, current_step
                        path=saver.save(sess, checkpoint_prefix, global_step=current_step)
                        logging.critical('Saved model {} at step {}'.format(path, best_at_step))
                        logging.critical('Best accuracy {} at step {}'.format(best_accuracy, best_at_step))
            logging.critical('Training is complete, testing the best model on x_test and y_test')
            
    # save trained params and files
    with open(trained_dir+'/words_index.json', 'w') as outfile:
        json.dump(vocabulary, outfile, indent=4, ensure_ascii=False)
    with open(trained_dir+'/embeddings.pickle', 'wb') as outfile:
        pickle.dump(embedding_mat, outfile, pickle.HIGHEST_PROTOCOL)
    with open(trained_dir+'/labels.json', 'w') as outfile:
        json.dump(label_dict, outfile, indent=4, ensure_ascii=False)
    params['sequence_length']=x_train.shape[1]
    with open(trained_dir+'/trained_parameters.json', 'w') as outfile:
        json.dump(params, outfile, indent=4, sort_keys=True, ensure_ascii=False)
    with open(trained_dir+'/classifier.pickle', 'wb') as outfile:
        pickle.dump(clf, outfile, pickle.HIGHEST_PROTOCOL)
        
        
def load_trained_params(trained_dir):
    params=json.loads(open(trained_dir+'trained_parameters.json').read())
    words_index=json.loads(open(trained_dir+'words_index.json').read())
    label_dict=json.loads(open(trained_dir+'labels.json').read())
    
    with open(trained_dir+'classifier.pickle', 'rb') as input_file:
        clf=pickle.load(input_file)
    with open(trained_dir+'embeddings.pickle', 'rb') as input_file:
        fetched_embedding=pickle.load(input_file)
    embedding_mat=np.array(fetched_embedding, dtype=np.float32)
    return params, words_index, label_dict, embedding_mat, clf


def load_test_data(test_file, label_dict):
    df=pd.read_csv(test_file, encoding='utf-8')
    select=['WYSText']
    
    df=df.dropna(axis=0, how='any', subset=select)
    test_examples=df[select[0]].apply(lambda x:data_helper_multi.clean_str(x).split(' ')).tolist()
    
    y_=None
    if 'category' in df.columns:
        select.append('category')
        y_=[]
        labels=label_dict.keys()
        for i in range(len(df)):
            y_line=[0]*len(label_dict.items())
            y_labels=df.iloc[i,0].strip(' ').split(' ')
            for y_label in y_labels:
                if y_label in labels:
                    y_line[label_dict[y_label]]=1
                else:
                    y_line[label_dict['<unk>']]=1
            y_.append(y_line)
        
    not_select=list(set(df.columns)-set(select))
    df=df.drop(not_select, axis=1)
    return test_examples, y_, df


def map_word_to_index(examples, words_index):
    x_=[]
    for example in examples:
        temp=[]
        for word in example:
            if word in words_index:
                temp.append(words_index[word])
            else:
                temp.append(0)
        x_.append(temp)
    return x_


def predict_cnn_rnn(demo_model, test_file):
    params, words_index, label_dict, embedding_mat, clf=load_trained_params('data_path_save/cnn_rnn_'+demo_model+'/trained_results/')
    x_, y_, df=load_test_data(test_file, label_dict)
    x_=data_helper_multi.pad_sentences(x_, forced_sequence_length=params['sequence_length'])
    x_=map_word_to_index(x_, words_index)
    reverse_dict=dict(zip(label_dict.values(),label_dict.keys()))
    
    x_test, y_test=np.asarray(x_), None
    if y_ is not None:
        y_test=np.asarray(y_)
        
    predicted_dir=os.path.join('data_path_save/cnn_rnn_'+demo_model,'predicted_results')
    if os.path.exists(predicted_dir):
        shutil.rmtree(predicted_dir)
    os.makedirs(predicted_dir)
    
    with tf.Graph().as_default():
        session_conf=tf.ConfigProto(allow_soft_placement=True, log_device_placement=False)
        sess=tf.Session(config=session_conf)
        with sess.as_default():
            cnn_rnn=TextCNNRNN(embedding_mat=embedding_mat, non_static=params['non_static'], hidden_unit=params['hidden_unit'], sequence_length=len(x_test[0]),
                               max_pool_size=params['max_pool_size'], filter_sizes=map(int, params['filter_sizes'].split(",")), num_filters=params['num_filters'], 
                               num_classes=len(label_dict.items()),embedding_size=params['embedding_dim'],l2_reg_lambda=params['l2_reg_lambda'])
            
            def real_len(batches):
                return [np.ceil(np.argmin(batch+[0])*1.0/params['max_pool_size']) for batch in batches]
            
            def predict_step(x_batch):
                feed_dict={
                        cnn_rnn.input_x: x_batch,
                        cnn_rnn.dropout_keep_prob: 1.0,
                        cnn_rnn.batch_size: len(x_batch),
                        cnn_rnn.pad: np.zeros([len(x_batch), 1, params['embedding_dim'], 1]),
                        cnn_rnn.real_len: real_len(x_batch)
                        }
                scores=sess.run([cnn_rnn.scores], feed_dict=feed_dict)
                return scores
            
            checkpoint_file=tf.train.latest_checkpoint('data_path_save/cnn_rnn_'+demo_model+'/checkpoints/')
            saver=tf.train.Saver(tf.all_variables())
            saver=tf.train.import_meta_graph('{}.meta'.format(checkpoint_file))
            saver.restore(sess, checkpoint_file)
            logging.critical('{} has been loaded'.format(checkpoint_file))
            
            batches=data_helper_multi.batch_iter(list(x_test), params['batch_size'], 1, shuffle=False)
            predictions, predicted_labels=[], []
            
            for x_batch in batches:
                batch_scores=predict_step(x_batch)[0]
                batch_predictions=clf.predict(batch_scores)
                for batch_prediction in batch_predictions:
                    predictions.append(batch_prediction)
                    line=[]
                    for i in range(len(batch_prediction)):
                        if batch_prediction[i]==1:
                            line.append(reverse_dict[i])
                    predicted_labels.append(line)
                    
            # save the predictions back to file
            df['NEW_PREDICTED']=predicted_labels
            columns=sorted(df.columns, reverse=True)
            df.to_csv(predicted_dir+'/predictions_all.csv', index=False, columns=columns)
            
            if y_test is not None:
                y_union=np.array(predictions)+y_test
                accuracy=float(np.sum(y_union==2))/float(np.sum(y_union==1))
                logging.critical('The prediction accuracy is: {}'.format(accuracy))
                
            logging.critical('Prediction is complete, all files have been saved: {}'.format(predicted_dir))


            
def demo_cnn_rnn(demo_model):
    # load training parameters
    params, words_index, label_dict, embedding_mat, clf=load_trained_params('data_path_save/cnn_rnn_'+demo_model+'/trained_results/')    
    reverse_dict=dict(zip(label_dict.values(),label_dict.keys()))
    
    with tf.Graph().as_default():
        session_conf=tf.ConfigProto(allow_soft_placement=True, log_device_placement=False)
        sess=tf.Session(config=session_conf)
        with sess.as_default():
            cnn_rnn=TextCNNRNN(embedding_mat=embedding_mat, non_static=params['non_static'], hidden_unit=params['hidden_unit'], sequence_length=params['sequence_length'],
                               max_pool_size=params['max_pool_size'], filter_sizes=map(int, params['filter_sizes'].split(",")), num_filters=params['num_filters'], 
                               num_classes=len(label_dict.items()),embedding_size=params['embedding_dim'],l2_reg_lambda=params['l2_reg_lambda'])
            
            def real_len(batches):
                return [np.ceil(np.argmin(batch+[0])*1.0/params['max_pool_size']) for batch in batches]
            
            def predict_step(x_batch):
                feed_dict={
                        cnn_rnn.input_x: x_batch,
                        cnn_rnn.dropout_keep_prob: 1.0,
                        cnn_rnn.batch_size: len(x_batch),
                        cnn_rnn.pad: np.zeros([len(x_batch), 1, params['embedding_dim'], 1]),
                        cnn_rnn.real_len: real_len(x_batch)
                        }
                scores=sess.run([cnn_rnn.scores], feed_dict=feed_dict)
                return scores
            
            checkpoint_file=tf.train.latest_checkpoint('data_path_save/cnn_rnn_'+demo_model+'/checkpoints/')
            saver=tf.train.Saver(tf.all_variables())
            saver=tf.train.import_meta_graph('{}.meta'.format(checkpoint_file))
            saver.restore(sess, checkpoint_file)
            logging.critical('{} has been loaded'.format(checkpoint_file))
            
            while(1):
                print('Please input your sentence:')
                input_sentence = input()
                if input_sentence == '' or input_sentence.isspace():
                    print('See you next time!')
                    break
                else:
                    x_=data_helper_multi.clean_str(input_sentence).split(' ')
                    # Prediction: cut off the sentence if it is longer than the sequence length
                    sequence_length=params['sequence_length']
                    num_padding=sequence_length-len(x_)
                    padded_sentence=[]
                    if num_padding<0:
                        logging.info('This sentence has to be cut off because it is longer than trained sequence length')
                        padded_sentence=x_[0: sequence_length]
                    else:
                        padded_sentence=x_+['<PAD/>']*num_padding
                    # Get word index
                    temp=[]
                    for word in padded_sentence:
                        if word in words_index:
                            temp.append(words_index[word])
                        else:
                            temp.append(0)
                    temp=np.asarray(temp)
                    x_test=np.expand_dims(temp, axis=0)
                    
                    scores=predict_step(x_test)[0][0]
                    pred=clf.predict(scores)
                    predicted_label=[]
                    for i in range(len(pred)):
                        if pred[i]==1:
                            predicted_label.append(reverse_dict[i])
                    print('\n疾病类别： '+predicted_label+'\n')



def get_cate_accuracy(demo_model):
    import matplotlib
    import matplotlib.pyplot as plt
    # 设置matplotlib正常显示中文和负号
    matplotlib.rcParams['font.sans-serif']=['SimHei']   # 用黑体显示中文
    
    df=pd.read_csv('data_path_save/cnn_rnn_'+demo_model+'/predicted_results/predictions_all.csv', encoding='utf8')
    labels=[]
    for i in range(len(df)):
        items=df.iloc[i,0]
        for item in items:
            labels.append(item)
    labels=list(set(labels))
#    labels=list(set(list(df['category'])))
    
    df2=pd.DataFrame(columns=['cate', 'correct', 'total', 'accuracy'])
    df2['cate']=labels
    df2['correct']=[0]*len(labels)
    df2['total']=[0]*len(labels)
    df2['accuracy']=[0]*len(labels)
    
    for i in range(len(df)):
        for j in range(len(df2)):
            if df.iloc[i,0]==df2.iloc[j,0]:
                if df.iloc[i,0]==df.iloc[i,2]:
                    df2.iloc[j,1]+=1
                df2.iloc[j,2]+=1
    
    df2['accuracy']=round(df2['correct']/df2['total'],2)
    plt.figure(figsize=(12,15))
    plt.barh(range(len(labels)), df2['accuracy'], height=0.7, color='steelblue', alpha=0.8)      # 从下往上画
    plt.yticks(range(len(labels)), labels)
    plt.xlabel("Accuracy")
    plt.title("各类别的准确率")
    for x, y in enumerate(df2['accuracy']):
        plt.text(y + 0.02, x - 0.1, '%s' % y)
    plt.show()
    df2.to_excel('data_path_save/cnn_rnn_'+demo_model+'/predicted_results/category_accuracy.xls', encoding='gb2312')      
                    

if __name__=='__main__':
    train_cnn_rnn(input_file='data_path/labeled_text_train2.csv', training_config='training_config.json')
#    predict_cnn_rnn(demo_model='1526871224',test_file='data_path/labeled_text_test2.csv')
#    get_cate_accuracy(demo_model='1526871224')
#    demo_cnn_rnn(demo_model='1526871224')
