# -*- coding: utf-8 -*-
from __future__ import division
from __future__ import print_function
import tensorflow as tf
import cells
from tensorflow.python.framework import ops
from tensorflow.python.ops import array_ops, embedding_ops, math_ops, nn_ops
from tensorflow.models.rnn import rnn_cell
from tensorflow.python.ops import variable_scope as vs

# from six.moves import xrange

VINYALS_KAISER = 'vinyals_kayser'
LUONG_GENERAL = 'luong_general'
LUONG_DOT = 'luong_dot'
MOD_VINYALS_KAISER = 'modified_vinyals_kayser'
MOD_VINYALS_KAISER_RELU = 'modified_vinyals_kayser_relu'
MOD_BAHDANAU = 'modified_bahdanau'
_SEED = 1234


# TODO: finish pydocs


def embedding_attention_decoder(decoder_inputs, initial_state, attention_states, cell,
                                batch_size, num_symbols, step_num, window_size=10, output_size=None,
                                output_projection=None, input_feeding=False, feed_previous=False,
                                attention_type=None, content_function=VINYALS_KAISER, output_attention=False,
                                dropout=None, initializer=None, decoder_states=None, beam_size=5,
                                dtype=tf.float32, scope=None):
    """
    RNN decoder with embedding and attention and a pure-decoding option.

    Parameters
    ----------

    decoder_inputs: list
            a list of 1D batch-sized int32 Tensors (decoder inputs).

    initial_state: tensor
            2D Tensor [batch_size x cell.state_size].

    attention_states: tensor
            3D Tensor [batch_size x attn_length x attn_size].

    cell: RNNCell
            rnn_cell.RNNCell defining the cell function.

    batch_size: tensor
            tensor representing the size of the batch when training the models

    num_symbols: int
            how many symbols come into the embedding.

    window_size : int
            size of the window when using local attention. Default to 10.

    output_size: int or None
            size of the output vectors; if None, use cell.output_size. Default to None.

    output_projection: None or tuple
            None or a pair (W, B) of output projection weights and
                biases; W has shape [output_size x num_symbols] and B has shape
                [num_symbols]; if provided and feed_previous=True, each fed previous
                output will first be multiplied by W and added B. Default to None.

    input_feeding : boolean
            Flag indicating where to use the "input feeding approach" proposed by Luong et al. (2015).
                Default to False.

    feed_previous: Boolean
            Boolean; if True, only the first of decoder_inputs will be
                used (the "GO" symbol), and all other decoder inputs will be generated by:
                next = embedding_lookup(embedding, argmax(previous_output)),
                In effect, this implements a greedy decoder. It can also be used
                during training to emulate http://arxiv.org/pdf/1506.03099v2.pdf.
                If False, decoder_inputs are used as given (the standard decoder case).
                Defaults to False.

    attention_type: string
            string indicating which type of attention to use. One of 'global', 'local' and 'hybrid'.
                Default to None.

    content_function: string
            The name of the content function to use when deriving the context (attention) vector.
                Default to 'vinyals_kayser'.

    output_attention: boolean
            Flag indicatin whether or not to apply the attention to the decoder states. Default to False.

    translate: boolean
            Flag indicating whether or not to run a translation step. If we are translating sentences rather than
                training the model, we use the alternative functions (those that end with '_search') so that we
                build the beam search inside the computational graph. Default to False.

    beam_size: int
            Size of the beam to use when running the translation steps.

    dtype:
            The dtype to use for the RNN initial states (default: tf.float32).

    scope: VariableScope
        VariableScope for the created subgraph; defaults to "embedding_attention_decoder".

    Returns
    -------

    outputs: list
            A list of the same length as decoder_inputs of 2D Tensors with
                shape [batch_size x output_size] containing the generated outputs.

    states: list
            The state of each decoder cell in each time-step. This is a list
                with length len(decoder_inputs) -- one item for each time-step.
                Each item is a 2D Tensor of shape [batch_size x cell.state_size].

    Raises
    ------
            ValueError: when output_projection has the wrong shape.
    """

    assert attention_type is not None

    if output_size is None:
        output_size = cell.output_size
    if output_projection is not None:
        proj_weights = ops.convert_to_tensor(output_projection[0], dtype=dtype)
        proj_weights.get_shape().assert_is_compatible_with([cell.output_size,
                                                            num_symbols])
        proj_biases = ops.convert_to_tensor(output_projection[1], dtype=dtype)
        proj_biases.get_shape().assert_is_compatible_with([num_symbols])

    if dropout is not None:

        for c in cell._cells:
            c.input_keep_prob = 1.0 - dropout

    if initializer is None:
        initializer = tf.random_uniform_initializer(minval=-0.1, maxval=0.1, seed=_SEED)

    with vs.variable_scope(scope or "embedding_attention_decoder", initializer=initializer):
        with ops.device("/cpu:0"):
            if input_feeding:
                embedding = vs.get_variable("embedding", [num_symbols, cell.input_size / 2])
            else:
                embedding = vs.get_variable("embedding", [num_symbols, cell.input_size])

        def extract_argmax_and_embed(prev, _):
            """Loop_function that extracts the symbol from prev and embeds it."""
            if output_projection is not None:
                prev = nn_ops.xw_plus_b(
                        prev, output_projection[0], output_projection[1])
            prev = array_ops.stop_gradient(math_ops.argmax(prev, 1))
            # x, prev_symbol = nn_ops.top_k(prev, 12)
            prev_symbol = array_ops.stop_gradient(prev)
            emb_prev = embedding_ops.embedding_lookup(embedding, prev_symbol)
            return emb_prev

        loop_function = None
        if feed_previous:
            loop_function = extract_argmax_and_embed

        emb_inp = [
            embedding_ops.embedding_lookup(embedding, i) for i in decoder_inputs]

        if output_attention:

             return _attention_decoder_output(
                    emb_inp, initial_state, attention_states, cell, batch_size, attention_type=attention_type,
                    output_size=output_size, loop_function=loop_function, window_size=window_size, beam_size=beam_size,
                    content_function=content_function, input_feeding=input_feeding, decoder_outputs=decoder_states,
                    step_num=step_num)
        else:
             return _attention_decoder(
                    emb_inp, initial_state, attention_states, cell, batch_size, attention_type=attention_type,
                    output_size=output_size, loop_function=loop_function, window_size=window_size,
                    input_feeding=input_feeding, content_function=content_function)


def _attention_decoder(decoder_inputs, initial_state, attention_states, cell, batch_size, attention_type=None,
                       output_size=None, loop_function=None, window_size=10, input_feeding=False, initializer=None,
                       combine_inp_attn=False, content_function=VINYALS_KAISER, dtype=tf.float32, scope=None):
    """

    Helper function implementing a RNN decoder with global, local or hybrid attention for the sequence-to-sequence
        model.

    Parameters
    ----------

    decoder_inputs: list
            a list of 2D Tensors [batch_size x cell.input_size].

    initial_state: tensor
            2d Tensor [batch_size x (number of decoder layers * hidden_layer_size * 2)] if LSTM or
            [batch_size x (number of decoder layers * hidden_layer_size)] if GRU representing the initial
                state (usually, we take the states of the encoder) to be used when running the decoder. The '2' on
                the LSTM formula mean that we have to set the hidden state and the cell state.

    attention_states: tensor
            3D tensor [batch_size x attn_length (time) x attn_size (hidden_layer_size)] representing the encoder
                hidden states that will be used to derive the context (attention) vector.

    cell: RNNCell
            rnn_cell.RNNCell defining the cell function and size.

    batch_size: tensor
            tensor representing the batch size used when training the model

    attention_type: string
            string indicating which type of attention to use. One of 'global', 'local' and 'hybrid'.
                Default to None.

    output_size: int
            size of the output vectors; if None, we use cell.output_size. Default to None.

    loop_function:
            if not None, this function will be applied to i-th output
                in order to generate i+1-th input, and decoder_inputs will be ignored,
                except for the first element ("GO" symbol). This can be used for decoding,
                but also for training to emulate http://arxiv.org/pdf/1506.03099v2.pdf.
            Signature -- loop_function(prev, i) = next
                * prev is a 2D Tensor of shape [batch_size x cell.output_size],
                * i is an integer, the step number (when advanced control is needed),
                * next is a 2D Tensor of shape [batch_size x cell.input_size].

    window_size: int
            size of the window to apply on local attention.Default to 10.

    input_feeding : boolean
            Flag indicating where to use the "input feeding approach" proposed by Luong et al. (2015).
                Default to False.

    content_function: string

    dtype:
            The dtype to use for the RNN initial state (default: tf.float32).

    scope:
            VariableScope for the created subgraph; default: "attention_decoder".

    Returns
    -------

    outputs:
            A list of the same length as decoder_inputs of 2D Tensors of shape
                [batch_size x output_size]. These represent the generated outputs.
                Output i is computed from input i (which is either i-th decoder_inputs or
                loop_function(output {i-1}, i)) as follows. First, we run the cell
                on a combination of the input and previous attention masks:
                    cell_output, new_state = cell(linear(input, prev_attn), prev_state).
                Then, we calculate new attention masks:
                    new_attn = softmax(V^T * tanh(W * attention_states + U * new_state))
                and then we calculate the output:
                    output = linear(cell_output, new_attn).

    states:
            The state of each decoder cell in each time-step. This is a list
                with length len(decoder_inputs) -- one item for each time-step.
                Each item is a 2D Tensor of shape [batch_size x cell.state_size].

    """
    if output_size is None:
        output_size = cell.output_size

    if initializer is None:
        initializer = tf.random_uniform_initializer(minval=-0.1, maxval=0.1, seed=_SEED)

    with vs.variable_scope(scope or "attention_decoder", initializer=initializer):

        batch = array_ops.shape(decoder_inputs[0])[0]  # Needed for reshaping.
        attn_length = attention_states.get_shape()[1].value
        attn_size = attention_states.get_shape()[2].value

        # To calculate W1 * h_t we use a 1-by-1 convolution, need to reshape before.
        hidden = array_ops.reshape(
                attention_states, [-1, attn_length, 1, attn_size])

        attention_vec_size = attn_size  # Size of query vectors for attention.

        va = None
        hidden_features = None

        if content_function is LUONG_GENERAL or content_function is VINYALS_KAISER:

            # for a in xrange(num_heads):
            # here we calculate the W_a * s_i-1 (W1 * h_1) part of the attention alignment
            k = vs.get_variable("AttnW_%d" % 0, [1, 1, attn_size, attention_vec_size], initializer=initializer)
            hidden_features = nn_ops.conv2d(hidden, k, [1, 1, 1, 1], "SAME")
            va = vs.get_variable("AttnV_%d" % 0, [attention_vec_size], initializer=initializer)

        elif content_function is MOD_VINYALS_KAISER or content_function is MOD_BAHDANAU:
            k = vs.get_variable("AttnW_%d" % 0, [1, 1, attn_size, 1], initializer=initializer)
            hidden_features = nn_ops.conv2d(hidden, k, [1, 1, 1, 1], "SAME")
        else:
            hidden_features = hidden

        cell_states = initial_state
        cell_outputs = []
        outputs = []
        prev = None
        batch_attn_size = array_ops.pack([batch, attn_size])

        # initial attention state
        ht_hat = array_ops.zeros(batch_attn_size, dtype=dtype)
        ht_hat.set_shape([None, attn_size])

        # cell_outputs.append(tf.zeros_like(ht_hat))

        for i in xrange(len(decoder_inputs)):
            if i > 0:
                vs.get_variable_scope().reuse_variables()

            if input_feeding:
                # if using input_feeding, concatenate previous attention with input to layers
                inp = array_ops.concat(1, [decoder_inputs[i], ht_hat])
            else:
                inp = decoder_inputs[i]

            # If loop_function is set, we use it instead of decoder_inputs.
            if loop_function is not None and prev is not None:
                with vs.variable_scope("loop_function", reuse=True, initializer=initializer):
                    inp = array_ops.stop_gradient(loop_function(prev, 12))

            if combine_inp_attn:
                # Merge input and previous attentions into one vector of the right size.
                x = cells.linear([inp] + [ht_hat], cell.input_size, True)
            else:
                x = inp

            # Run the RNN.
            cell_output, new_state = cell(x, cell_states)
            cell_states = new_state
            # states.append(new_state)  # new_state = dt#
            cell_outputs.append(cell_output)

            # dt = new_state
            if content_function is MOD_BAHDANAU:
                dt = cell_outputs[-2]
            else:
                dt = cell_output

            # Run the attention mechanism.
            if attention_type is 'local':
                ht_hat = _local_attention(decoder_hidden_state=dt,
                                          hidden_features=hidden_features, va=va, hidden_attn=hidden,
                                          attention_vec_size=attention_vec_size, attn_length=attn_length,
                                          attn_size=attn_size, batch_size=batch_size, content_function=content_function,
                                          window_size=window_size, initializer=initializer, dtype=dtype)

            elif attention_type is 'global':
                ht_hat = _global_attention(decoder_hidden_state=dt,
                                           hidden_features=hidden_features, v=va, hidden_attn=hidden,
                                           attention_vec_size=attention_vec_size, attn_length=attn_length,
                                           content_function=content_function,  initializer=initializer,
                                           attn_size=attn_size)

            else:  # here we choose the hybrid mechanism
                ht_hat = _hybrid_attention(decoder_hidden_state=dt,
                                           hidden_features=hidden_features, va=va, hidden_attn=hidden,
                                           attention_vec_size=attention_vec_size, attn_length=attn_length,
                                           attn_size=attn_size, batch_size=batch_size,
                                           content_function=content_function, window_size=window_size, dtype=dtype)

            #
            with vs.variable_scope("AttnOutputProjection", initializer=initializer):

                # if we pass a list of tensors, linear will first concatenate them over axis 1
                output = cells.linear([cell_output] + [ht_hat], output_size, True)

                output = tf.tanh(output)

            if loop_function is not None:
                # We do not propagate gradients over the loop function.
                prev = array_ops.stop_gradient(output)

            outputs.append(output)

    cell_outputs = tf.concat(0, cell_outputs)

    return outputs, cell_states, cell_outputs


def _hybrid_attention(decoder_hidden_state, hidden_features, va, hidden_attn, attention_vec_size,
                      attn_length, attn_size, batch_size, initializer, window_size=10,
                      content_function=VINYALS_KAISER, dtype=tf.float32):
    """

    Parameters
    ----------
    decoder_hidden_state
    hidden_features
    va
    hidden_attn
    attention_vec_size
    attn_length
    attn_size
    batch_size
    window_size
    content_function
    last_layer_output
    dtype

    Returns
    -------

    """

    local_attn = _local_attention(decoder_hidden_state=decoder_hidden_state,
                                  hidden_features=hidden_features, va=va, hidden_attn=hidden_attn,
                                  attention_vec_size=attention_vec_size, attn_length=attn_length,
                                  attn_size=attn_size, batch_size=batch_size, content_function=content_function,
                                  window_size=window_size, initializer=initializer, dtype=dtype)

    global_attn = _global_attention(decoder_hidden_state=decoder_hidden_state,
                                    hidden_features=hidden_features, v=va, hidden_attn=hidden_attn,
                                    attention_vec_size=attention_vec_size, attn_length=attn_length,
                                    content_function=content_function, attn_size=attn_size, initializer=initializer)

    with vs.variable_scope("FeedbackGate_%d" % 0, initializer=initializer):
        y = cells.linear(decoder_hidden_state, attention_vec_size, True)
        y = array_ops.reshape(y, [-1, 1, 1, attention_vec_size])

        vb = vs.get_variable("FeedbackVb_%d" % 0, [attention_vec_size], initializer=initializer)

        # tanh(Wp*ht)
        tanh = math_ops.tanh(y)
        beta = math_ops.sigmoid(math_ops.reduce_sum((vb * tanh), [2, 3]))

        attns = beta * global_attn + (1 - beta) * local_attn

    return attns


def _global_attention(decoder_hidden_state, hidden_features, v, hidden_attn, attention_vec_size, attn_length,
                      attn_size, initializer, content_function=VINYALS_KAISER):

    """
    Put global attention masks on hidden using hidden_features and query.

    Parameters
    ----------
    decoder_hidden_state
    hidden_features
    v
    hidden_attn
    attention_vec_size
    attn_length
    attn_size
    content_function
    last_layer_output

    Returns
    -------

    """

    with vs.variable_scope("Attention_%d" % 0, initializer=initializer):

        # a = None

        if content_function is LUONG_DOT:

            s = math_ops.reduce_sum((hidden_features * decoder_hidden_state), [2, 3])  # hidden features are h_s

            # a = tf.matmul(last_layer_output, hidden_features)

        elif content_function is LUONG_GENERAL:

            s = math_ops.reduce_sum((decoder_hidden_state * hidden_features), [2, 3])  # hidden features are Wa*h_s

        elif content_function is MOD_VINYALS_KAISER or content_function is MOD_BAHDANAU:

            y = cells.linear(decoder_hidden_state, 1, True)
            y = array_ops.reshape(y, [-1, 1, 1, 1])

            # Attention mask is a softmax of v^T * tanh(...).
            s = math_ops.reduce_sum(math_ops.tanh(hidden_features + y), [2, 3])

        elif content_function is MOD_VINYALS_KAISER_RELU:

            y = cells.linear(decoder_hidden_state, attention_vec_size, True)
            y = array_ops.reshape(y, [-1, 1, 1, attention_vec_size])

            # Attention mask is a softmax of v^T * tanh(...).
            s = math_ops.reduce_sum(v * tf.nn.relu(hidden_features + y), [2, 3])
        else:

            y = cells.linear(decoder_hidden_state, attention_vec_size, True)
            y = array_ops.reshape(y, [-1, 1, 1, attention_vec_size])

            # Attention mask is a softmax of v^T * tanh(...).
            s = math_ops.reduce_sum(v * math_ops.tanh(hidden_features + y), [2, 3])

        a = nn_ops.softmax(s)

        # Now calculate the attention-weighted vector d.
        d = math_ops.reduce_sum(
                array_ops.reshape(a, [-1, attn_length, 1, 1]) * hidden_attn,
                [1, 2])
        ds = array_ops.reshape(d, [-1, attn_size])

    return ds


def _local_attention(decoder_hidden_state, hidden_features, va, hidden_attn, attention_vec_size,
                     attn_length, attn_size, batch_size, initializer, window_size=10, content_function=VINYALS_KAISER,
                     dtype=tf.float32):
    """
    Put local attention masks on hidden using hidden_features and query.

    Parameters
    ----------
    decoder_hidden_state
    hidden_features
    va
    hidden_attn
    attention_vec_size
    attn_length
    attn_size
    batch_size
    window_size
    content_function
    last_layer_output
    dtype

    Returns
    -------

    """

    sigma = window_size / 2
    denominator = sigma ** 2

    with vs.variable_scope("AttentionLocal_%d" % 0, initializer=initializer):

        a = None
        ht = None

        if content_function is LUONG_DOT:

            s = math_ops.reduce_sum((decoder_hidden_state * hidden_features), [2, 3])

        elif content_function is LUONG_GENERAL:

            # related to the prediction of window center
            ht = cells.linear([decoder_hidden_state], attention_vec_size, True)

            s = math_ops.reduce_sum((decoder_hidden_state * hidden_features), [2, 3])

        elif content_function is MOD_VINYALS_KAISER or content_function is MOD_BAHDANAU:

            y = cells.linear(decoder_hidden_state, 1, True)
            y = array_ops.reshape(y, [-1, 1, 1, 1])

            # Attention mask is a softmax of v^T * tanh(...).
            s = math_ops.reduce_sum(math_ops.tanh(hidden_features + y), [2, 3])

        elif content_function is MOD_VINYALS_KAISER_RELU:

            # this code calculate the W2*dt part of the equation and also the Wp*ht of the prediction of window center
            linear_trans = cells.linear([decoder_hidden_state, decoder_hidden_state], attention_vec_size * 2, True)
            y, ht = tf.split(1, 2, linear_trans)

            y = array_ops.reshape(y, [-1, 1, 1, attention_vec_size])
            ht = array_ops.reshape(ht, [-1, 1, 1, attention_vec_size])

            # Attention mask is a softmax of v^T * tanh(W1*h_1 + W2*decoder_hidden_state)
            # reduce_sum is representing the + of (W1*h_1 + W2*decoder_hidden_state)
            # W1*h1 = hidden_features
            # W2*dt = y
            s = math_ops.reduce_sum(va * tf.nn.relu(hidden_features + y), [2, 3])
        else:

            # this code calculate the W2*dt part of the equation and also the Wp*ht of the prediction of window center
            linear_trans = cells.linear([decoder_hidden_state, decoder_hidden_state], attention_vec_size * 2, True)
            y, ht = tf.split(1, 2, linear_trans)

            y = array_ops.reshape(y, [-1, 1, 1, attention_vec_size])
            ht = array_ops.reshape(ht, [-1, 1, 1, attention_vec_size])

            # Attention mask is a softmax of v^T * tanh(W1*h_1 + W2*decoder_hidden_state)
            # reduce_sum is representing the + of (W1*h_1 + W2*decoder_hidden_state)
            # W1*h1 = hidden_features
            # W2*dt = y
            s = math_ops.reduce_sum(va * math_ops.tanh(hidden_features + y), [2, 3])

        # get the parameters (vp)
        vp = vs.get_variable("AttnVp_%d" % 0, [attention_vec_size], initializer=initializer)

        # tanh(Wp*ht)
        tanh = math_ops.tanh(ht)
        # S * sigmoid(vp * tanh(Wp*ht))  - this is going to return a number
        # for each sentence in the batch - i.e., a tensor of shape batch x 1
        S = attn_length
        pt = math_ops.reduce_sum((vp * tanh), [2, 3])
        pt = math_ops.sigmoid(pt) * S

        # now we get only the integer part of the values
        pt = tf.floor(pt)

        # we now create a tensor containing the indices representing each position
        # of the sentence - i.e., if the sentence contain 5 tokens and batch_size is 3,
        # the resulting tensor will be:
        # [[0, 1, 2, 3, 4]
        #  [0, 1, 2, 3, 4]
        #  [0, 1, 2, 3, 4]]
        #
        indices = []
        for pos in xrange(attn_length):
            indices.append(pos)
        indices = indices * batch_size
        idx = tf.convert_to_tensor(tf.to_float(indices), dtype=dtype)
        idx = tf.reshape(idx, [-1, attn_length])

        # here we calculate the boundaries of the attention window based on the ppositions
        low = pt - window_size + 1  # we add one because the floor op already generates the first position
        high = pt + window_size

        # here we check our positions against the boundaries
        mlow = tf.to_float(idx < low)
        mhigh = tf.to_float(idx > high)

        # now we combine both into a pre-mask that has 0s and 1s switched
        # i.e, at this point, True == 0 and False == 1
        m = mlow + mhigh  # batch_size

        # here we switch the 0s to 1s and the 1s to 0s
        # we correct the values so True == 1 and False == 0
        mask = tf.to_float(tf.equal(m, 0.0))

        # here we switch off all the values that fall outside the window
        # first we switch off those in the truncated normal
        a = s * mask
        masked_soft = nn_ops.softmax(a)

        # here we calculate the 'truncated normal distribution'
        numerator = -tf.pow((idx - pt), tf.convert_to_tensor(2, dtype=dtype))
        div = tf.truediv(numerator, denominator)
        e = math_ops.exp(div)  # result of the truncated normal distribution

        at = masked_soft * e

        # Now calculate the attention-weighted vector d.
        d = math_ops.reduce_sum(
                array_ops.reshape(at, [-1, attn_length, 1, 1]) * hidden_attn,
                [1, 2])
        ds = array_ops.reshape(d, [-1, attn_size])

    return ds


def _attention_decoder_output(decoder_inputs, initial_state, attention_states, cell, batch_size, step_num,
                              attention_type=None, output_size=None, loop_function=None, window_size=10, beam_size=5,
                              combine_inp_attn=False, input_feeding=False, content_function=VINYALS_KAISER,
                              initializer=None, decoder_outputs=None, dtype=tf.float32, scope=None):
    """

    Helper function implementing a RNN decoder with global, local or hybrid attention for the sequence-to-sequence
        model.

    Parameters
    ----------

    decoder_inputs: list
            a list of 2D Tensors [batch_size x cell.input_size].

    initial_state: tensor
            3D Tensor [batch_size x attn_length x attn_size].

    attention_states:

    cell: RNNCell
            rnn_cell.RNNCell defining the cell function and size.

    batch_size: int
            batch size when training the model

    attention_type: string
            string indicating which type of attention to use. One of 'global', 'local' and 'hybrid'. Default to None.

    output_size: int
            size of the output vectors; if None, we use cell.output_size.

    loop_function:
            if not None, this function will be applied to i-th output
                in order to generate i+1-th input, and decoder_inputs will be ignored,
                except for the first element ("GO" symbol). This can be used for decoding,
                but also for training to emulate http://arxiv.org/pdf/1506.03099v2.pdf.
            Signature -- loop_function(prev, i) = next
                * prev is a 2D Tensor of shape [batch_size x cell.output_size],
                * i is an integer, the step number (when advanced control is needed),
                * next is a 2D Tensor of shape [batch_size x cell.input_size].

    window_size: int
            size of the window to apply on local attention

    input_feeding: boolean
            whether or not to use the input feeding approach by Luong et al., 2015.

    content_function: string

    dtype:
            The dtype to use for the RNN initial state (default: tf.float32).

    scope:
            VariableScope for the created subgraph; default: "attention_decoder".

    Returns
    -------

    outputs:
            A list of the same length as decoder_inputs of 2D Tensors of shape
                [batch_size x output_size]. These represent the generated outputs.
                Output i is computed from input i (which is either i-th decoder_inputs or
                loop_function(output {i-1}, i)) as follows. First, we run the cell
                on a combination of the input and previous attention masks:
                    cell_output, new_state = cell(linear(input, prev_attn), prev_state).
                Then, we calculate new attention masks:
                    new_attn = softmax(V^T * tanh(W * attention_states + U * new_state))
                and then we calculate the output:
                    output = linear(cell_output, new_attn).

    states:
            The state of each decoder cell in each time-step. This is a list
                with length len(decoder_inputs) -- one item for each time-step.
                Each item is a 2D Tensor of shape [batch_size x cell.state_size].

    """
    if output_size is None:
        output_size = cell.output_size

    if initializer is None:
        initializer = tf.random_uniform_initializer(minval=-0.1, maxval=0.1, seed=_SEED)

    with vs.variable_scope(scope or "attention_decoder", initializer=initializer):

        batch = array_ops.shape(decoder_inputs[0])[0]  # Needed for reshaping.
        attn_length = attention_states.get_shape()[1].value
        attn_size = attention_states.get_shape()[2].value

        # To calculate W1 * h_t we use a 1-by-1 convolution, need to reshape before.
        hidden = array_ops.reshape(
                attention_states, [-1, attn_length, 1, attn_size])

        attention_vec_size = attn_size  # Size of query vectors for attention.

        va = None
        hidden_features = None

        if content_function is LUONG_GENERAL or content_function is VINYALS_KAISER:

            # for a in xrange(num_heads):
            # here we calculate the W_a * s_i-1 (W1 * h_1) part of the attention alignment
            k = vs.get_variable("AttnW_%d" % 0, [1, 1, attn_size, attention_vec_size], initializer=initializer)
            hidden_features = nn_ops.conv2d(hidden, k, [1, 1, 1, 1], "SAME")
            va = vs.get_variable("AttnV_%d" % 0, [attention_vec_size], initializer=initializer)

        elif content_function is MOD_VINYALS_KAISER or content_function is MOD_BAHDANAU:
            k = vs.get_variable("AttnW_%d" % 0, [1, 1, attn_size, 1], initializer=initializer)
            hidden_features = nn_ops.conv2d(hidden, k, [1, 1, 1, 1], "SAME")
        else:
            hidden_features = hidden

        cell_state = initial_state

        outputs = []
        prev = None
        batch_attn_size = array_ops.pack([batch, attn_size])

        # initial attention state
        ht_hat = array_ops.zeros(batch_attn_size, dtype=dtype)
        ht_hat.set_shape([None, attn_size])

        if decoder_outputs is None:
            cell_outputs = []
        else:
            cell_outputs = decoder_outputs
            # if tf.equal(step_num, 0):
            #     cell_outputs = tf.split(0, step_num, decoder_outputs)
            # else:
            #     cell_outputs = tf.split(0, 1, decoder_outputs)
            # cell_outputs = tf.split(0, step_num.eval(), decoder_outputs)

        for i in xrange(len(decoder_inputs)):
            if i > 0:
                vs.get_variable_scope().reuse_variables()

            if input_feeding:
                # if using input_feeding, concatenate previous attention with input to layers
                inp = array_ops.concat(1, [decoder_inputs[i], ht_hat])
            else:
                inp = decoder_inputs[i]

            # If loop_function is set, we use it instead of decoder_inputs.
            if loop_function is not None and prev is not None:
                with vs.variable_scope("loop_function", reuse=True, initializer=initializer):
                    inp = array_ops.stop_gradient(loop_function(prev, 12))

            if combine_inp_attn:
                # Merge input and previous attentions into one vector of the right size.
                x = cells.linear([inp] + [ht_hat], cell.input_size, True)
            else:
                x = inp

            # Run the RNN.
            cell_output, new_state = cell(x, cell_state)
            cell_state = new_state

            if decoder_outputs is None:

                # states.append(new_state)  # new_state = dt#
                cell_outputs.append(cell_output)

            else:
                reshaped = tf.reshape(cell_output, [-1, 1, 1, attn_size])
                decoder_outputs = tf.concat(1, [decoder_outputs, reshaped])

            # dt = new_state
            if content_function is MOD_BAHDANAU:
                dt = cell_outputs[-2]
            else:
                dt = cell_output

            # Run the attention mechanism.
            if attention_type is 'local':  # local attention
                ht_hat = _local_attention(
                    decoder_hidden_state=dt, hidden_features=hidden_features, va=va, hidden_attn=hidden,
                    attention_vec_size=attention_vec_size, attn_length=attn_length, attn_size=attn_size,
                    batch_size=batch_size, content_function=content_function, window_size=window_size,
                    initializer=initializer, dtype=dtype
                )

            elif attention_type is 'global':  # global attention
                ht_hat = _global_attention(
                    decoder_hidden_state=dt, hidden_features=hidden_features, v=va,
                    hidden_attn=hidden, attention_vec_size=attention_vec_size,
                    attn_length=attn_length, content_function=content_function,
                    attn_size=attn_size, initializer=initializer
                )

            else:  # here we choose the hybrid mechanism
                ht_hat = _hybrid_attention(
                    decoder_hidden_state=dt, hidden_features=hidden_features, va=va, hidden_attn=hidden,
                    attention_vec_size=attention_vec_size, attn_length=attn_length, attn_size=attn_size,
                    batch_size=batch_size, content_function=content_function, window_size=window_size,
                    initializer=initializer, dtype=dtype
                )

            with vs.variable_scope("AttnOutputProjection", initializer=initializer):

                if decoder_outputs is None:

                    shape1 = len(cell_outputs)

                    top_states = [tf.reshape(o, [-1, 1, attn_size]) for o in cell_outputs]

                    output_attention_states = tf.concat(1, top_states)

                    decoder_hidden = array_ops.reshape(output_attention_states, [-1, shape1, 1, attn_size])

                    ht = decoder_output_attention(decoder_hidden, attn_size, initializer=initializer)
                else:

                    # dec_outs = tf.reshape(decoder_outputs, tf.pack([-1, step_num, 1, attn_size]))
                    #
                    # decoder_hidden = dec_outs

                    decoder_hidden = decoder_outputs

                    ht = decoder_output_attention(decoder_hidden, attn_size,
                                                  initializer=initializer,
                                                  step_num=step_num)

                output = cells.linear([ht] + [ht_hat], output_size, True)

                output = tf.tanh(output)

            if loop_function is not None:
                # We do not propagate gradients over the loop function.
                prev = array_ops.stop_gradient(output)

            outputs.append(output)

    if decoder_outputs is None:

        cell_outs = [tf.reshape(o, [-1, 1, 1, attn_size]) for o in cell_outputs]

        cell_outputs = tf.concat(1, cell_outs)
    else:
        cell_outputs = decoder_outputs

    # cell_outputs = tf.concat(0, cell_outputs)

    return outputs, cell_state, cell_outputs


def decoder_output_attention(decoder_hidden, attn_size, initializer, step_num=None):
    """

    Parameters
    ----------
    decoder_states
    attn_size

    Returns
    -------

    """

    # attn_length = len(decoder_states)
    # shape = decoder_hidden.get_shape()
    # timesteps = shape[1].value  # how many timesteps we have

    with vs.variable_scope("decoder_output_attention", initializer=initializer):
        #
        # top_states = [tf.reshape(o, [-1, 1, attn_size]) for o in decoder_states]
        #
        # output_attention_states = tf.concat(1, top_states)
        #
        # decoder_hidden = array_ops.reshape(output_attention_states, [-1, attn_length, 1, attn_size])

        k = vs.get_variable("AttnDecW_%d" % 0, [1, 1, attn_size, attn_size], initializer=initializer)
        hidden_features = nn_ops.conv2d(decoder_hidden, k, [1, 1, 1, 1], "SAME")
        v = vs.get_variable("AttnDecV_%d" % 0, [attn_size])

        # s will be (?, timesteps)
        s = math_ops.reduce_sum((v * math_ops.tanh(hidden_features)), [2, 3])
        # s = math_ops.reduce_sum(math_ops.tanh(hidden_features), [2, 3])

        # beta will be (?, timesteps)
        beta = nn_ops.softmax(s)

        if step_num is None:  # step_num is None when training

            shape = decoder_hidden.get_shape()
            timesteps = shape[1].value
            b = array_ops.reshape(beta, [-1, timesteps, 1, 1])

        else:

            b = array_ops.reshape(beta, tf.pack([-1, step_num, 1, 1]))

        # b  and decoder_hidden will be (?, timesteps, 1, 1)
        d = math_ops.reduce_sum(b * decoder_hidden, [1, 2])

        # d will be (?, decoder_size)
        ds = tf.reshape(d, [-1, attn_size])

    # ds is (?, decoder_size)
    return ds
