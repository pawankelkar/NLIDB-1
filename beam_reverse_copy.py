from __future__ import print_function
import sys
import os
import keras
from tensorflow.python.platform import gfile
import numpy as np
import tensorflow as tf
from tensorflow.python.layers.core import Dense
from utils.both import load_data,load_vocab_all
from utils.bleu import moses_multi_bleu
from collections import defaultdict
import sys
reload(sys)
sys.setdefaultencoding('utf8')

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
# ----------------------------------------------------------------------------
'''
TODO:
l2_scale regularizer
'''
_PAD = 0
_GO = 1
_END = 2
epochs = 100
lr = 0.0001
BS = 128
maxlen = 20
embedding_dim = 300
D = embedding_dim
n_states = int(D/2)
T = maxlen
in_drop=.0
out_drop=.0
vocabulary_size=10949
embedding_size=300
subset='all'
load_model=True
input_vocab_size = vocabulary_size
output_vocab_size = vocabulary_size
dim = n_states
# ----------------------------------------------------------------------------
def train(sess, env, X_data, y_data, epochs=10, load=False, shuffle=True, batch_size=BS,
          name='model',base=0):
    if load:
        print('\nLoading saved model')
        env.saver.restore(sess, model2Bload )

    print('\nTrain model')
    n_sample = X_data.shape[0]
    n_batch = int((n_sample+batch_size-1) / batch_size)
    for epoch in range(epochs):
        print('\nEpoch {0}/{1}'.format(epoch+1, epochs))
        sys.stdout.flush()
        if shuffle:
            print('\nShuffling data')
            ind = np.arange(n_sample)
            np.random.shuffle(ind)
            X_data = X_data[ind]
            y_data = y_data[ind]


        for batch in range(n_batch):
            print(' batch {0}/{1}'.format(batch+1, n_batch),end='\r')
            start = batch * batch_size
            end = min(n_sample, start+batch_size)
            sess.run(env.train_op, feed_dict={env.x: X_data[start:end],
                                              env.y: y_data[start:end],
                                              env.training: True})
        evaluate(sess, env, X_data, y_data, batch_size=batch_size)

        if (epoch+1)==epochs:
            print('\n Saving model')
            env.saver.save(sess, 'reverse_model/{0}-{1}'.format(name,base))
    return 'reverse_model/{0}-{1}'.format(name,base) 

def evaluate(sess, env, X_data, y_data, batch_size=BS):
    """
    Evaluate TF model by running env.loss and env.acc.
    """
    print('\nEvaluating')

    n_sample = X_data.shape[0]
    n_batch = int((n_sample+batch_size-1) / batch_size)
    loss, acc = 0, 0

    for batch in range(n_batch):
        print(' batch {0}/{1}'.format(batch+1, n_batch),end='\r')
        sys.stdout.flush()
        start = batch * batch_size
        end = min(n_sample, start+batch_size)
        cnt = end - start
        batch_loss, batch_acc = sess.run(
            [env.loss,env.acc],
            feed_dict={env.x: X_data[start:end],
                       env.y: y_data[start:end]})
        loss += batch_loss * cnt
        acc += batch_acc * cnt
    loss /= n_sample
    acc /= n_sample

    print(' loss: {0:.4f} acc: {1:.4f}'.format(loss, acc))
    return acc

#---------------------------------------------------------------------------
class Dummy:
    pass
env = Dummy()

from tf_utils.attention_wrapper import AttentionWrapper,BahdanauAttention
from tf_utils.beam_search_decoder import BeamSearchDecoder
from tf_utils.decoder import dynamic_decode
from tf_utils.basic_decoder import BasicDecoder
def _decoder( encoder_outputs , encoder_state , mode , beam_width , batch_size):
    
    num_units = 2*dim
    # [batch_size, max_time,...]
    memory = encoder_outputs
    
    if mode == "infer":
        memory = tf.contrib.seq2seq.tile_batch( memory, multiplier=beam_width )
        encoder_state = tf.contrib.seq2seq.tile_batch( encoder_state, multiplier=beam_width )
        batch_size = batch_size * beam_width
    else:
        batch_size = batch_size

    seq_len = tf.tile(tf.constant([maxlen], dtype=tf.int32), [ batch_size ] )
    attention_mechanism = BahdanauAttention( num_units = num_units, memory=memory, 
                                                               normalize=True,
                                                               memory_sequence_length=seq_len)

    cell0 = tf.contrib.rnn.GRUCell( 2*dim )
    cell = tf.contrib.rnn.DropoutWrapper(cell0, input_keep_prob=1-in_drop,output_keep_prob=1-out_drop)
    cell = AttentionWrapper( cell,
                                                attention_mechanism,
                                                attention_layer_size=num_units,
                                                name="attention")

    decoder_initial_state = cell.zero_state(batch_size, tf.float32).clone( cell_state=encoder_state )

    return cell, decoder_initial_state


def Decoder( mode , enc_rnn_out , enc_rnn_state , X,  emb_Y , emb_out):
    
    with tf.variable_scope("Decoder") as decoder_scope:

        mem_units = 2*dim
        out_layer = Dense( output_vocab_size ) #projection W*X+b
        beam_width = 5
        batch_size = tf.shape(enc_rnn_out)[0]

        cell , initial_state = _decoder( enc_rnn_out ,enc_rnn_state  , mode , beam_width ,batch_size)
        

        if mode == "train":

            seq_len = tf.tile(tf.constant([maxlen], dtype=tf.int32), [ batch_size ] )
            #[None]/[batch_size]
            helper = tf.contrib.seq2seq.TrainingHelper( inputs = emb_Y , sequence_length = seq_len )
            decoder = BasicDecoder( cell = cell, helper = helper, initial_state = initial_state,X=X, output_layer=out_layer) 
            outputs, final_state, _= tf.contrib.seq2seq.dynamic_decode(decoder=decoder, maximum_iterations=maxlen, scope=decoder_scope)
            logits = outputs.rnn_output
            sample_ids = outputs.sample_id
        else:

            start_tokens = tf.tile(tf.constant([_GO], dtype=tf.int32), [ batch_size ] )
            end_token = _END

            my_decoder = BeamSearchDecoder( cell = cell,
                                                               embedding = emb_out,
                                                               start_tokens = start_tokens,
                                                               end_token = end_token,
                                                               initial_state = initial_state,
                                                               beam_width = beam_width,
                                                               X = X,
                                                               output_layer = out_layer ,
                                                               length_penalty_weight=0.0 )
                      
            outputs, t1 , t2 = tf.contrib.seq2seq.dynamic_decode(  my_decoder, maximum_iterations=maxlen,scope=decoder_scope )
            logits = tf.no_op()
            sample_ids = outputs.predicted_ids
        
    return logits , sample_ids

#----------------------------------------------------------------------------------------------
def construct_graph(mode,env=env):

    _, _, vocab_emb = load_vocab_all()
    print('Vocab size:')
    print(vocab_emb.shape)
    emb_out = tf.get_variable( "emb_out" , initializer=vocab_emb)
    emb_X = tf.nn.embedding_lookup( emb_out , env.x ) 
    emb_Y = tf.nn.embedding_lookup( emb_out , env.y )
    #[None, 20, 300]


    with tf.name_scope("Encoder"):
        cell_fw0 = tf.contrib.rnn.GRUCell(dim)
        cell_fw = tf.contrib.rnn.DropoutWrapper(cell_fw0, input_keep_prob=1-in_drop,output_keep_prob=1-out_drop)
        cell_bw0 = tf.contrib.rnn.GRUCell(dim)
        cell_bw = tf.contrib.rnn.DropoutWrapper(cell_bw0, input_keep_prob=1-in_drop,output_keep_prob=1-out_drop)

        enc_rnn_out , enc_rnn_state = tf.nn.bidirectional_dynamic_rnn( cell_fw , cell_bw , emb_X , dtype=tf.float32)
        #state: (output_state_fw, output_state_bw) 
        #([None, 20, 150],[None, 20, 150])
        enc_rnn_out = tf.concat(enc_rnn_out, 2)
        #[None,20,300]
        enc_rnn_state = tf.concat([enc_rnn_state[0],enc_rnn_state[1]],axis=1)

    logits , sample_ids = Decoder(mode, enc_rnn_out , enc_rnn_state , env.x , emb_Y, emb_out)
    if mode == 'train':
	    env.pred = tf.concat( (env.y[:,1:],tf.zeros((tf.shape(env.y)[0],1), dtype=tf.int32)),axis=1)
	    env.loss = tf.losses.softmax_cross_entropy(  tf.one_hot( env.pred, output_vocab_size ) , logits )
	    optimizer = tf.train.AdamOptimizer(lr)
	    optimizer.minimize(env.loss)
	    gvs = optimizer.compute_gradients(env.loss)
	    capped_gvs = [(tf.clip_by_norm(grad, 5.), var) for grad, var in gvs]
	    env.train_op = optimizer.apply_gradients(capped_gvs)

	    a = tf.equal( sample_ids , env.pred )
	    b = tf.reduce_all(a, axis=1)
	    env.acc = tf.reduce_mean( tf.cast( b , dtype=tf.float32 ) ) 
    else:
	    #[None,sentence length,beam_width]
	    sample_ids = tf.transpose( sample_ids , [0,2,1] )
	    #[None,beam_width,sentence length]
	    env.acc = None
	    env.loss = None
	    env.train_op = None 
        
    return env.train_op , env.loss , env.acc , sample_ids , logits



def decode_data(sess, X_data, y_data , batch_size = BS):
    print('\nDecoding')
    n_sample = X_data.shape[0]
    n_batch = int((n_sample+batch_size-1) / batch_size)
    acc = 0
    true_values , values = [], []
    _,reverse_vocab_dict,_=load_vocab_all()
    with gfile.GFile('output.txt', mode='w') as output:
        for batch in range(n_batch):
            print(' batch {0}/{1}'.format(batch+1, n_batch),end='\r')
            sys.stdout.flush()
            start = batch * batch_size
            end = min(n_sample, start+batch_size)
            cnt = end - start
            ybar = sess.run(
                pred_ids,
                feed_dict={env.x: X_data[start:end]})
            xtru = X_data[start:end]
            ytru = y_data[start:end]
            ybar = np.asarray(ybar)
            ybar = np.squeeze(ybar[:,0,:])
            #print(ybar.shape)
            for true_seq,seq,x in zip(ytru, ybar, xtru):
                true_seq=true_seq[1:]
                try:
                    true_seq=true_seq[:list(true_seq).index(2)]
                except ValueError:
                    pass
                try:
                    seq=seq[:list(seq).index(2)]
                except ValueError:
                    pass
                xseq = " ".join([reverse_vocab_dict[idx] for idx in x ])
                logic=" ".join([reverse_vocab_dict[idx] for idx in seq ])
                true_logic=" ".join([reverse_vocab_dict[idx] for idx in true_seq ])
                acc+=(logic==true_logic)
                if False and logic != true_logic:
                    output.write('-----\n')
                    output.write(xseq+'\n')
                    output.write(true_logic+'\n')
                    output.write(logic+'\n')
                true_values.append(true_logic)
                values.append(logic)        
               
    print('EM count acc:%.4f'%(acc*1./len(y_data)))  
    true_values, values= np.asarray(true_values), np.asarray(values)
    bleu_score = moses_multi_bleu(true_values,values)
    print('bleu score:%.4f'%bleu_score)
def decode_one(sent_file):
    vocab_dict,reverse_vocab_dict,_=load_vocab_all()
    reverse_vocab_dict[-1]='pad'
    X_data = []
    with gfile.GFile(sent_file,mode='r') as sf:
        lines = sf.readlines()
        for sent in lines:
            x_data = [_GO]
    	    x_data.extend([vocab_dict[x] for x in sent.split()])
            x_data.append(_END)
            x_data.extend([_PAD  for x in range(maxlen-len(x_data))])
            X_data.append(x_data)
    X_data = np.asarray(X_data)
    ybar = sess.run(
            pred_ids,
            feed_dict={env.x: X_data})
    ybar=np.asarray(ybar)
    print(ybar.shape)
    for i,seq_per_beam in enumerate(ybar):
        print('=========SQL==========')
        true = " ".join([reverse_vocab_dict[idx] for idx in X_data[i] ])
        print(true)
        for i,seq in enumerate(seq_per_beam):
            for j,word in enumerate(seq):
                if word==_END:
                    break
            seq = seq[:j]
            logic=" ".join([reverse_vocab_dict[idx] for idx in seq ])
            print('beam '+str(i+1)+':'+logic)

#----------------------------------------------------------------------
y_train,X_train=load_data(maxlen=maxlen,load=True,s='train')
y_test,X_test=load_data(maxlen=maxlen,load=True,s='test')
y_dev,X_dev=load_data(maxlen=maxlen,load=True,s='dev')
model2Bload = 'reverse_model/{}'.format(subset)
for base in range(10):
        print('~~~~~~~~~~~~~~~~~%d~~~~~~~~~~~~~~~~~~~~~'%base)
        tf.reset_default_graph()
        train_graph = tf.Graph()
        infer_graph = tf.Graph()
        
        with train_graph.as_default():
            env.x = tf.placeholder( tf.int32 , shape=[None,maxlen], name='x' )
            env.y = tf.placeholder(tf.int32, (None, maxlen), name='y')
            env.training = tf.placeholder_with_default(False, (), name='train_mode')
            env.train_op, env.loss , env.acc, sample_ids,logits = construct_graph("train")
            env.saver = tf.train.Saver()

            sess = tf.InteractiveSession()
            sess.run(tf.global_variables_initializer())
            sess.run(tf.local_variables_initializer())
            epochs = 5
            model2Bload = train(sess, env, X_train, y_train, epochs = epochs,load=load_model,name=subset,batch_size=BS,base=base)
            load_model = True
        
        with infer_graph.as_default():
            env.x = tf.placeholder( tf.int32 , shape=[None,maxlen], name='x' )
            env.y = tf.placeholder(tf.int32, (None, maxlen), name='y')
            env.training = tf.placeholder_with_default(False, (), name='train_mode')   
            _ , env.loss , env.acc , pred_ids, _ = construct_graph("infer")
            env.infer_saver = tf.train.Saver()

            sess = tf.InteractiveSession()
            env.infer_saver.restore(sess, model2Bload )
            decode_data(sess, X_train, y_train)
            decode_data(sess, X_dev, y_dev)
           
            
            


